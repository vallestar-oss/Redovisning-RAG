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

from .search import SearchResult
from .vectorstore import COLLECTION_NAME, EMBEDDING_MODEL, embed_query

# Kandidatdjup per delsökning innan fusionen. Måste vara klart större än
# top_k för att fusionen ska ha något att arbeta med.
_CANDIDATE_DEPTH = 50
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


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


@dataclass
class QueryFilters:
    company: str | None = None
    fiscal_year: str | None = None

    def as_chroma_where(self) -> dict | None:
        clauses = []
        if self.company:
            clauses.append({"company": self.company})
        if self.fiscal_year:
            clauses.append({"fiscal_year": self.fiscal_year})
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
    """
    lowered = query.lower()

    company = next((c for c in sorted(company_years) if c in lowered), None)

    if company:
        valid_years = company_years[company]
    else:
        valid_years = set().union(*company_years.values()) if company_years else set()

    years = [m.group(0) for m in _YEAR_RE.finditer(query)]
    candidates: list[str] = []
    # Brutet räkenskapsår skrivet som 2023/24, 2023/2024 eller 2023-24
    for m in re.finditer(r"((?:19|20)\d{2})\s*[/\-–]\s*(\d{2}|\d{4})", query):
        candidates.append(f"{m.group(1)}-{m.group(2)[-2:]}")
    candidates.extend(years)
    # Ett ensamt årtal kan också avse ett brutet räkenskapsår som SLUTAR
    # det året (SkiStars "2024" = räkenskapsåret 2023-24).
    candidates.extend(f"{int(y) - 1}-{y[-2:]}" for y in years)

    fiscal_year = next((c for c in candidates if c in valid_years), None)

    return QueryFilters(company=company, fiscal_year=fiscal_year)


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


class HybridSearcher:
    """Laddar modell, Chroma-collection och BM25-index en gång."""

    def __init__(
        self,
        persist_dir: Path,
        chunks_dir: Path,
        collection_name: str = COLLECTION_NAME,
    ):
        self._client = chromadb.PersistentClient(path=str(persist_dir))
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

    def _vector_ids(self, query: str, where: dict | None, depth: int) -> list[str]:
        res = self._collection.query(
            query_embeddings=[embed_query(self._model, query)],
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
        if not filters.company and not filters.fiscal_year:
            return None
        return {
            cid
            for cid, c in self._chunks.items()
            if (not filters.company or c["company"] == filters.company)
            and (not filters.fiscal_year or c["fiscal_year"] == filters.fiscal_year)
        }

    def _to_result(self, chunk_id: str) -> SearchResult:
        c = self._chunks[chunk_id]
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
        self, query: str, top_k: int = 5, use_filters: bool = True
    ) -> list[SearchResult]:
        filters = (
            parse_query_filters(query, self._company_years)
            if use_filters
            else QueryFilters()
        )

        where = filters.as_chroma_where()
        allowed = self._allowed_ids(filters)

        vector_ids = self._vector_ids(query, where, _CANDIDATE_DEPTH)
        bm25_ids = self._bm25_ids(query, allowed, _CANDIDATE_DEPTH)

        # Ett feltolkat filter får inte tömma resultatet - falla tillbaka
        # på ofiltrerad sökning hellre än att svara "hittade inget".
        if len(vector_ids) + len(bm25_ids) < _MIN_FILTERED_HITS and where is not None:
            vector_ids = self._vector_ids(query, None, _CANDIDATE_DEPTH)
            bm25_ids = self._bm25_ids(query, None, _CANDIDATE_DEPTH)

        fused = _rrf_fuse([vector_ids, bm25_ids])
        return [self._to_result(cid) for cid in fused[:top_k] if cid in self._chunks]


if __name__ == "__main__":
    import sys

    from .search import format_result

    root = Path(__file__).resolve().parent.parent
    searcher = HybridSearcher(root / "data" / "chroma", root / "data" / "chunks")
    user_query = " ".join(sys.argv[1:]) or "Vad var Volvos summa tillgångar 2023?"
    parsed = parse_query_filters(user_query, searcher._company_years)
    print(f"Fråga: {user_query}")
    print(f"Filter: bolag={parsed.company or '-'}  räkenskapsår={parsed.fiscal_year or '-'}\n")
    for i, result in enumerate(searcher.search(user_query, top_k=5), 1):
        print(format_result(result, i))
        print()
