"""Hybridsökning: metadatafiltrering + vektorsökning + nyckelordssökning.

Bakgrund (docs/DECISIONS_FAS3.md): ren vektorsökning har enstaka
katastroffall även med den bästa utvärderade modellen - SkiStars "summa
tillgångar" hamnade på rank 42. Frågorna i vårt scope bär dock starka
LEXIKALA signaler som vektorsökningen inte utnyttjar:

    "Vad var SkiStars nettoomsättning 2023/24?"
         |            |               |
      bolag        nyckeltal        räkenskapsår

Den här modulen utnyttjar alla tre:

1. **Metadatafilter** - bolag och räkenskapsår tolkas ur frågan och används
   som hårt filter mot Chromas metadata. Filtret sätts BARA när tolkningen
   är entydig och matchar värden som faktiskt finns i indexet; annars körs
   sökningen ofiltrerad. Ger dessutom en automatisk återgång till
   ofiltrerad sökning om filtret gav för få träffar.
2. **Vektorsökning** - semantisk likhet (multilingual-e5-base).
3. **BM25** - lexikalisk matchning, som fångar exakta termer där
   embeddingen är osäker ("nettoomsättning" vs "nettoinvesteringar").

Vektor- och BM25-listorna slås ihop med Reciprocal Rank Fusion (RRF).
RRF valdes för att den bara använder RANGORDNING, inte poäng - de två
systemens poängskalor är inte jämförbara och kräver då ingen normalisering
med godtyckliga vikter.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

import chromadb
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer

from .chunking import _display_company
from .progress import ProgressCallback, emit
from .search import SearchResult
from .vectorstore import COLLECTION_NAME, EMBEDDING_MODEL, embed_query

# Kandidatdjup per delsökning innan fusionen. Måste vara klart större än
# top_k för att fusionen ska ha något att arbeta med.
#
# Höjt från 50 till 100 efter mätning (docs/DECISIONS_FAS4.md): en post som
# krävs för att beräkna ett nyckeltal ("Rörelseresultat" för
# rörelsemarginal) rankades ~75-82 i både BM25 och vektorsökningen, trots
# att expand_query() lade till exakt rätt sökterm. Orsaken är att
# fakta-chunkarna är korta och strukturellt likartade ("X var N (2023), M
# (2022)."), så tusentals andra rader delar samma boilerplate-fraser och
# tränger undan den rätta posten inom ett djup på bara 50.
_CANDIDATE_DEPTH = 100
# RRF-konstant. Styr hur mycket en topplacering premieras: ett lågt k gör
# skillnaden mellan rank 1 och rank 15 stor, ett högt k gör dem nästan
# likvärdiga.
#
# Standardvärdet från originalartikeln (60) är avstämt för webbskala med
# många rankare. Med bara två rankare över en filtrerad kandidatmängd blev
# det fel här: vektorsökningen rankar självsäkert men fel (den skiljer inte
# "summa tillgångar" från "summa skulder"), och med k=60 räckte dess
# förstaplats för att rösta ner BM25:s korrekta förstaplats till rank 8.
#
# Uppmätt på facit (se docs/DECISIONS_FAS3.md):
#
#   k       Recall@1  Recall@3  Recall@5
#   1-3       62 %      88 %     100 %
#   5         62 %     100 %     100 %
#   10-20     62 %      88 %     100 %
#   40-60     75 %      88 %      88 %
#
# Valt k=5. Höga k ger bättre Recall@1, men missar en fråga helt (rank 8) -
# och i ett RAG-system läser språkmodellen ALLA topp-k chunkar, så att
# svaret finns med i kontexten (Recall@5) väger tyngre än att det ligger
# exakt först. Hela bandet k=1..20 ger Recall@5=100 %, så valet är inte
# känsligt för exakt värde.
_RRF_K = 5
# Under så här många träffar anses metadatafiltret ha varit för snävt
# (t.ex. feltolkat år) och sökningen görs om utan filter.
_MIN_FILTERED_HITS = 3

_YEAR_RE = re.compile(r"(19|20)\d{2}")
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)

# Nyckeltal som ska BERÄKNAS (docs/SCOPE.md) förekommer sällan ordagrant i
# källmaterialet - "rörelsemarginal" är inte en textsträng som står i någon
# rad, bara "Rörelseresultat" och "Nettoomsättning" var för sig. Uppmätt: en
# fråga om "rörelsemarginal" hittade Nettoomsättning på rank 5 men
# Rörelseresultat först på rank 41 (utanför även top_k=20) - varken BM25
# eller embeddingen kopplar ihop kvotens NAMN med sina ingående POSTER. En
# högre top_k löser därför inte problemet, den späder bara ut kontexten.
#
# Fixen är att EXPANDERA frågan med posternas egna namn innan sökningen körs,
# så att båda termerna får en chans att matcha lexikalt (BM25) och
# semantiskt (embedding). Formlerna är hämtade direkt ur docs/SCOPE.md.
_RATIO_TERM_EXPANSIONS: dict[str, list[str]] = {
    "bruttomarginal": ["bruttovinst", "nettoomsättning", "omsättning"],
    "rörelsemarginal": ["rörelseresultat", "nettoomsättning", "omsättning"],
    "vinstmarginal": ["nettoresultat", "årets resultat", "nettoomsättning", "omsättning"],
    "soliditet": ["eget kapital", "summa tillgångar"],
    "skuldsättningsgrad": ["summa skulder", "eget kapital"],
    "kassalikviditet": ["omsättningstillgångar", "varulager", "kortfristiga skulder"],
    "roe": ["nettoresultat", "eget kapital"],
    "avkastning på eget kapital": ["nettoresultat", "eget kapital"],
    "roa": ["nettoresultat", "summa tillgångar"],
    "avkastning på totalt kapital": ["nettoresultat", "summa tillgångar"],
    "fritt kassaflöde": ["kassaflöde från den löpande verksamheten", "investeringar"],
    "ebitda": ["rörelseresultat", "avskrivningar"],
}


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def expand_query(query: str) -> str:
    """Lägger till underliggande postnamn när frågan nämner ett beräknat
    nyckeltal. Returnerar frågan oförändrad annars."""
    lowered = query.lower()
    extra_terms: list[str] = []
    for ratio, terms in _RATIO_TERM_EXPANSIONS.items():
        if ratio in lowered:
            extra_terms.extend(terms)
    if not extra_terms:
        return query
    return f"{query} " + " ".join(dict.fromkeys(extra_terms))


@dataclass
class QueryFilters:
    company: str | None = None
    # Flera år stöds explicit (inte bara ett) för flerårs-/YoY-frågor. Att
    # bara stänga av filtret helt när >1 år nämns visade sig otillräckligt:
    # utan NÅGOT årsfilter vinner ofta ett HELT ANNAT dokuments chunkar,
    # eftersom varje rad redan innehåller föregående års jämförelsetal
    # ("X var A (2024), B (2023)") - volvo_2025.pdf:s rader nämner alltså
    # "2024" lika ofta som volvo_2024.pdf:s gör. Filtret måste därför
    # begränsa till ALLA nämnda år (matcha vilket som helst av dem), inte
    # bara till inget alls. Se docs/DECISIONS_FAS4.md.
    fiscal_years: frozenset[str] = frozenset()

    def as_chroma_where(self) -> dict | None:
        clauses = []
        if self.company:
            clauses.append({"company": self.company})
        if len(self.fiscal_years) == 1:
            clauses.append({"fiscal_year": next(iter(self.fiscal_years))})
        elif len(self.fiscal_years) > 1:
            clauses.append({"fiscal_year": {"$in": sorted(self.fiscal_years)}})
        if not clauses:
            return None
        return clauses[0] if len(clauses) == 1 else {"$and": clauses}


def parse_query_filters(
    query: str, company_years: dict[str, set[str]]
) -> QueryFilters:
    """Tolkar bolag och räkenskapsår ur frågan.

    Bolagsnamn matchas som delsträng, vilket hanterar svenska genitivformer
    och sammansättningar utan egen ordlista ("Hexatronics", "Volvos",
    "Volvokoncernen", "SkiStars" innehåller alla sitt bolagsnamn).

    Räkenskapsåret valideras mot DET IDENTIFIERADE BOLAGETS år, inte mot
    alla år i indexet. Det spelar roll eftersom SkiStar har brutet
    räkenskapsår: frågan "SkiStars nettoomsättning 2024" ska bli
    "2023-24" (året som slutar i augusti 2024), inte "2024" - som inte
    finns för SkiStar och skulle ge noll träffar.

    Sätts inget kandidatvärde som faktiskt finns sätts inget årsfilter -
    hellre ofiltrerat än fel filtrerat.

    Frågan kan nämna FLERA olika giltiga räkenskapsår ("...från 2023 till
    2024?", "...utvecklingen 2023-2025?", prioritet 2 i docs/SCOPE.md).
    Samtliga nämnda år tas då med i filtret ($in), inte bara det första -
    annars försvinner alla år utom ett ur träffmängden. Att ta bort
    årsfiltret helt visade sig otillräckligt (se docs/DECISIONS_FAS4.md):
    utan NÅGOT årsfilter vinner ofta fel dokument, eftersom varje rad redan
    innehåller föregående års jämförelsetal ("X var A (2024), B (2023)") -
    volvo_2025.pdf:s rader nämner alltså "2024" lika ofta som
    volvo_2024.pdf:s gör.

    Två skilda skrivsätt måste särskiljas:
    - Brutet räkenskapsår ("2023/24", kort tvåsiffrigt slutår) - EN period.
    - Årsintervall ("2023-2025", fullständigt fyrsiffrigt slutår) - FLERA
      hela kalenderår, ett per år i intervallet.
    """
    lowered = query.lower()

    company = next((c for c in sorted(company_years) if c in lowered), None)

    if company:
        valid_years = company_years[company]
    else:
        valid_years = set().union(*company_years.values()) if company_years else set()

    consumed_spans: list[tuple[int, int]] = []
    candidates: list[str] = []
    # \d{4} provas FÖRE \d{2} i alternativet - annars matchar regexen bara
    # de två första siffrorna av ett fyrsiffrigt slutår.
    for m in re.finditer(r"((?:19|20)\d{2})\s*[/\-–]\s*(\d{4}|\d{2})", query):
        start, end_raw = m.group(1), m.group(2)
        consumed_spans.append(m.span())
        if len(end_raw) == 2:
            # Kort slutår: brutet räkenskapsår, en enda period.
            candidates.append(f"{start}-{end_raw}")
        else:
            # Fullständigt slutår: ett intervall av hela kalenderår.
            for y in range(int(start), int(end_raw) + 1):
                candidates.append(str(y))
                # Varje år i intervallet kan också vara slutet på ett brutet
                # räkenskapsår för bolag som har sådant.
                candidates.append(f"{y - 1}-{str(y)[-2:]}")

    def _already_consumed(match: re.Match) -> bool:
        return any(start <= match.start() and match.end() <= end for start, end in consumed_spans)

    standalone_years = [
        m.group(0) for m in _YEAR_RE.finditer(query) if not _already_consumed(m)
    ]
    candidates.extend(standalone_years)
    # Ett fristående årtal kan också avse ett brutet räkenskapsår som
    # SLUTAR det året (SkiStars "2024" = räkenskapsåret 2023-24).
    candidates.extend(f"{int(y) - 1}-{y[-2:]}" for y in standalone_years)

    matched_years = frozenset(c for c in candidates if c in valid_years)

    return QueryFilters(company=company, fiscal_years=matched_years)


def _rrf_fuse(ranked_lists: list[list[str]], k: int = _RRF_K) -> list[str]:
    """Reciprocal Rank Fusion.

    Poänglika kandidater bryts på chunk_id. Utan det avgörs deras inbördes
    ordning av i vilken ordning dellistorna råkade behandlas, så samma
    fråga kunde ge olika resultatordning mellan körningar - oacceptabelt
    för en sökfunktion som ska gå att felsöka och testa.
    """
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, chunk_id in enumerate(ranked, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores, key=lambda cid: (-scores[cid], cid))


def _describe_sources(results: list[SearchResult]) -> str | None:
    """Kort sammanfattning av var träffarna kommer ifrån, för statusraden i
    UI:t: "volvo_2023.pdf s. 62, 63 · volvo_2024.pdf s. 37"."""
    if not results:
        return None
    pages_by_doc: dict[str, list[int]] = {}
    for r in results:
        seen = pages_by_doc.setdefault(r.document, [])
        for p in r.pages:
            if p not in seen:
                seen.append(p)
    return " · ".join(
        f"{doc} s. {', '.join(str(p) for p in sorted(pages))}"
        for doc, pages in pages_by_doc.items()
    )


class HybridSearcher:
    """Laddar modell, Chroma-collection och BM25-index en gång."""

    def __init__(
        self,
        persist_dir: Path | None,
        chunks_dir: Path,
        collection_name: str = COLLECTION_NAME,
        client: chromadb.ClientAPI | None = None,
    ):
        # `client` är ett explicit injektionspunkt (samma mönster som
        # LLMProvider i src/llm.py): anroparen avgör moln vs lokalt, aldrig
        # en dold miljövariabel-koll här. Annars skulle testsviten (som
        # pekar mot den lokala data/chroma-mappen) tyst börja träffa
        # Chroma Cloud så fort CHROMA_API_KEY finns i .env.
        self._client = client or chromadb.PersistentClient(path=str(persist_dir))
        self._collection = self._client.get_collection(collection_name)
        self._model = SentenceTransformer(EMBEDDING_MODEL)

        self._chunks: dict[str, dict] = {}
        for path in sorted(chunks_dir.glob("*.json")):
            for chunk in json.loads(path.read_text(encoding="utf-8")):
                self._chunks[chunk["chunk_id"]] = chunk

        self._ids = list(self._chunks)
        self._bm25 = BM25Okapi([_tokenize(self._chunks[i]["text"]) for i in self._ids])
        self._company_years: dict[str, set[str]] = {}
        for c in self._chunks.values():
            self._company_years.setdefault(c["company"], set()).add(c["fiscal_year"])

    def _vector_ids(self, embedding: list[float], where: dict | None, depth: int) -> list[str]:
        # Tar en färdig embedding, inte frågetexten: search() bäddar in en
        # gång och återanvänder resultatet även i den ofiltrerade
        # fallback-sökningen (som annars bäddade in exakt samma fråga igen).
        res = self._collection.query(
            query_embeddings=[embedding],
            n_results=depth,
            where=where,
            include=[],
        )
        return res["ids"][0]

    def _bm25_ids(self, query: str, allowed: set[str] | None, depth: int) -> list[str]:
        scores = self._bm25.get_scores(_tokenize(query))
        order = sorted(range(len(self._ids)), key=lambda i: -scores[i])
        out = []
        for i in order:
            chunk_id = self._ids[i]
            if allowed is not None and chunk_id not in allowed:
                continue
            if scores[i] <= 0:
                break
            out.append(chunk_id)
            if len(out) >= depth:
                break
        return out

    def _allowed_ids(self, filters: QueryFilters) -> set[str] | None:
        if not filters.company and not filters.fiscal_years:
            return None
        return {
            cid
            for cid, c in self._chunks.items()
            if (not filters.company or c["company"] == filters.company)
            and (not filters.fiscal_years or c["fiscal_year"] in filters.fiscal_years)
        }

    def _to_result(self, chunk_id: str) -> SearchResult:
        c = self._chunks.get(chunk_id)
        if c is None:
            # Chroma Cloud-kvoten tvingade tre stora tabellchunkar
            # (Volvos balansräkningar) att delas vid molnuppladdningen
            # (src/migrate_to_cloud.py::_split_oversized), med id:n som
            # "<original>::del1", "::del2" osv. Den lokala chunks_dir-datan
            # är oförändrad och känner bara till originalet - slå upp på
            # bas-id:t istället, så svaret ändå får hela tabellen (bättre
            # kontext än en halv tabell) och rätt källhänvisning.
            base_id = re.sub(r"::del\d+$", "", chunk_id)
            c = self._chunks[base_id]
        return SearchResult(
            chunk_id=chunk_id,
            document=c["document"],
            company=c["company"],
            fiscal_year=c["fiscal_year"],
            section=c["section"] or "",
            chunk_type=c["chunk_type"],
            pages=list(c["pages"]),
            text=c["text"],
            # Fusionen rangordnar, den producerar inget jämförbart avstånd.
            distance=float("nan"),
        )

    def search(
        self,
        query: str,
        top_k: int = 5,
        use_filters: bool = True,
        on_progress: ProgressCallback | None = None,
    ) -> list[SearchResult]:
        filters = (
            parse_query_filters(query, self._company_years)
            if use_filters
            else QueryFilters()
        )

        where = filters.as_chroma_where()
        allowed = self._allowed_ids(filters)

        if filters.company or filters.fiscal_years:
            parts = []
            if filters.company:
                parts.append(_display_company(filters.company))
            if filters.fiscal_years:
                parts.append(", ".join(sorted(filters.fiscal_years)))
            emit(on_progress, "filters", "Tolkade frågan", " · ".join(parts))
        else:
            emit(
                on_progress,
                "filters",
                "Tolkade frågan",
                "inget specifikt bolag/år - söker i allt underlag",
            )

        # Expansionen görs EFTER filtertolkningen (som ska läsa frågan
        # skriven av användaren, inte de tillagda posttermerna) men
        # FÖRE själva sökningen, så både BM25 och embeddingen ser termerna.
        search_query = expand_query(query)
        if search_query != query:
            emit(
                on_progress,
                "expansion",
                "Kompletterade sökningen med nyckeltalets underliggande poster",
            )

        emit(on_progress, "embedding", "Skapar embedding av frågan", EMBEDDING_MODEL)
        embedding = embed_query(self._model, search_query)

        emit(on_progress, "vector", "Söker i vektordatabasen")
        vector_ids = self._vector_ids(embedding, where, _CANDIDATE_DEPTH)

        emit(on_progress, "bm25", "Nyckelordssökning (BM25)")
        bm25_ids = self._bm25_ids(search_query, allowed, _CANDIDATE_DEPTH)

        # Ett feltolkat filter får inte tömma resultatet - falla tillbaka
        # på ofiltrerad sökning hellre än att svara "hittade inget".
        if len(vector_ids) + len(bm25_ids) < _MIN_FILTERED_HITS and where is not None:
            emit(
                on_progress,
                "fallback",
                "För få träffar med filtret - söker om utan det",
            )
            vector_ids = self._vector_ids(embedding, None, _CANDIDATE_DEPTH)
            bm25_ids = self._bm25_ids(search_query, None, _CANDIDATE_DEPTH)

        fused = _rrf_fuse([vector_ids, bm25_ids])

        # Bas-id-avdubbling: en delad chunk (se _to_result) kan dyka upp två
        # gånger i fused - en gång som "::delN" (via vektorsökningen mot
        # molnet) och en gång som originalet (via BM25, som alltid indexerar
        # den lokala helheten). Båda pekar på identiskt samma svarstext, så
        # utan avdubbling skulle en post i top_k slösas på en dublett.
        results: list[SearchResult] = []
        seen_base_ids: set[str] = set()
        for cid in fused:
            base_id = cid if cid in self._chunks else re.sub(r"::del\d+$", "", cid)
            if base_id not in self._chunks or base_id in seen_base_ids:
                continue
            seen_base_ids.add(base_id)
            results.append(self._to_result(cid))
            if len(results) >= top_k:
                break

        emit(
            on_progress,
            "results",
            f"Hittade {len(results)} relevanta avsnitt",
            _describe_sources(results),
        )
        return results


if __name__ == "__main__":
    import sys

    from .search import format_result

    root = Path(__file__).resolve().parent.parent
    searcher = HybridSearcher(root / "data" / "chroma", root / "data" / "chunks")
    user_query = " ".join(sys.argv[1:]) or "Vad var Volvos summa tillgångar 2023?"
    parsed = parse_query_filters(user_query, searcher._company_years)
    years_label = ", ".join(sorted(parsed.fiscal_years)) or "-"
    print(f"Fråga: {user_query}")
    print(f"Filter: bolag={parsed.company or '-'}  räkenskapsår={years_label}\n")
    for i, result in enumerate(searcher.search(user_query, top_k=5), 1):
        print(format_result(result, i))
        print()
