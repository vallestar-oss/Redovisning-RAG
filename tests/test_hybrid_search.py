"""Tester för hybridsökningen (Fas 3): frågetolkning, fusion och en
retrieval-kvalitetsspärr mot ett facit.

Kvalitetsspärren är avsiktligt en del av testsviten och inte bara ett
engångsexperiment: retrieval är enligt researchen systemets vanligaste
flaskhals, och en tyst försämring (ny modell, ändrad chunkning, annan
RRF-konstant) är annars svår att upptäcka.
"""

import json
from pathlib import Path

import pytest

from src.hybrid_search import HybridSearcher, _rrf_fuse, expand_query, parse_query_filters

ROOT = Path(__file__).resolve().parent.parent
CHUNKS_DIR = ROOT / "data" / "chunks"
CHROMA_DIR = ROOT / "data" / "chroma"

# Bolag -> räkenskapsår, speglar indexets faktiska innehåll. SkiStar har
# brutet räkenskapsår, övriga kalenderår.
COMPANY_YEARS = {
    "hexatronic": {"2023", "2024", "2025"},
    "volvo": {"2023", "2024", "2025"},
    "skistar": {"2022-23", "2023-24", "2024-25"},
}

# (fråga, godkända chunk_id) - facit för retrieval-kvaliteten.
GROUND_TRUTH = [
    ("Vad var Hexatronics nettoomsättning 2023?",
     {"hexatronic_2023.pdf::resultaträkning::rad0"}),
    ("Vad var Hexatronics summa tillgångar 2024?",
     {"hexatronic_2024.pdf::balansräkning::rad25"}),
    ("Vad var SkiStars nettoomsättning 2023/24?",
     {"skistar_2023-24.pdf::resultaträkning::rad0"}),
    ("Vad var SkiStars summa tillgångar 2023/24?",
     {"skistar_2023-24.pdf::balansräkning::rad28"}),
    ("Vad var Volvos rörelseresultat 2024?",
     {"volvo_2024.pdf::resultaträkning::rad9",
      "volvo_2024.pdf::resultaträkning::rad38",
      "volvo_2024.pdf::resultaträkning::rad56"}),
    ("Vad var Volvos summa tillgångar 2023?",
     {"volvo_2023.pdf::balansräkning::rad23"}),
    ("Vad var Hexatronics rörelseresultat 2025?",
     {"hexatronic_2025.pdf::resultaträkning::rad10"}),
    ("Vad var SkiStars kassaflöde från den löpande verksamheten 2024/25?",
     {"skistar_2024-25.pdf::kassaflödesanalys::rad7"}),
]


# --- frågetolkning (kräver inget index) ---------------------------------

@pytest.mark.parametrize(
    "query, company, fiscal_years",
    [
        ("Vad var Hexatronics nettoomsättning 2023?", "hexatronic", {"2023"}),
        ("Vad var Volvos rörelseresultat 2024?", "volvo", {"2024"}),
        ("Volvokoncernens eget kapital 2023", "volvo", {"2023"}),
        ("Vad var SkiStars nettoomsättning 2023/24?", "skistar", {"2023-24"}),
        # Ensamt årtal för bolag med brutet räkenskapsår ska tolkas som det
        # räkenskapsår som SLUTAR det året, inte som ett kalenderår som inte
        # finns för bolaget.
        ("SkiStars nettoomsättning 2024", "skistar", {"2023-24"}),
        ("SkiStars summa tillgångar 2025", "skistar", {"2024-25"}),
        # Årtal utanför materialet ska inte ge ett årsfilter alls.
        ("SkiStars omsättning 2019", "skistar", set()),
        # Ingen entydig signal alls -> inget filter.
        ("Hur har soliditeten utvecklats?", None, set()),
        # Flera olika giltiga år i samma fråga (YoY/flerårstrend, prioritet 2
        # i docs/SCOPE.md) ska ge ETT FILTER SOM TÄCKER ALLA nämnda år, inte
        # bara det första och inte inget filter alls - se
        # docs/DECISIONS_FAS4.md för varför "inget filter" inte räcker.
        ("Hur har Hexatronics nettoomsättning utvecklats 2023-2025?",
         "hexatronic", {"2023", "2024", "2025"}),
        ("Hur har Volvos rörelseresultat förändrats från 2023 till 2024?",
         "volvo", {"2023", "2024"}),
        # Regressionsfall: ett brutet räkenskapsår ("2023/24") är EN period,
        # inte två - ska fortsätta ge ett exakt filter, inte tolkas som en
        # flerårsfråga bara för att uttrycket innehåller två siffergrupper.
        ("SkiStars nettoomsättning 2023/24", "skistar", {"2023-24"}),
    ],
)
def test_parse_query_filters(query, company, fiscal_years):
    filters = parse_query_filters(query, COMPANY_YEARS)
    assert filters.company == company
    assert filters.fiscal_years == frozenset(fiscal_years)


def test_filters_build_valid_chroma_where():
    both = parse_query_filters("Volvos omsättning 2024", COMPANY_YEARS)
    assert both.as_chroma_where() == {
        "$and": [{"company": "volvo"}, {"fiscal_year": "2024"}]
    }
    only_company = parse_query_filters("Volvos omsättning", COMPANY_YEARS)
    assert only_company.as_chroma_where() == {"company": "volvo"}
    assert parse_query_filters("omsättning", COMPANY_YEARS).as_chroma_where() is None

    multi_year = parse_query_filters("Volvos omsättning 2023 till 2024", COMPANY_YEARS)
    assert multi_year.as_chroma_where() == {
        "$and": [{"company": "volvo"}, {"fiscal_year": {"$in": ["2023", "2024"]}}]
    }


def test_expand_query_uses_company_specific_net_result_term():
    """Bolagsspecifik terminologi (fixar N5, docs/evaluation.md): Volvo
    kallar sin nettoresultat-rad "Periodens resultat", ALDRIG "Årets
    resultat" som Hexatronic och SkiStar gör - bekräftat mot samtliga tre
    bolags faktiska fakta-chunkar i data/chunks/. En gemensam synonymlista
    med båda varianterna för alla bolag mättes (fristående BM25-körning,
    utan HybridSearcher) FÖRSÄMRA ett redan fungerande fall (Hexatronics
    vinstmarginal) genom att "resultat" är så vanligt att det späder ut
    BM25-poängen brett - se motivering i src/hybrid_search.py. Expansionen
    ska därför vara bolagsmedveten: bara det aktuella bolagets EGEN term."""
    volvo_query = expand_query("Vad var Volvos vinstmarginal 2023?", "volvo")
    assert "periodens resultat" in volvo_query.lower()
    assert "årets resultat" not in volvo_query.lower()

    hexatronic_query = expand_query("Vad var Hexatronics vinstmarginal 2024?", "hexatronic")
    assert "årets resultat" in hexatronic_query.lower()
    assert "periodens resultat" not in hexatronic_query.lower()

    skistar_query = expand_query("Vad var SkiStars vinstmarginal 2023/24?", "skistar")
    assert "årets resultat" in skistar_query.lower()
    assert "periodens resultat" not in skistar_query.lower()

    # Okänt/oidentifierat bolag - hellre en bred gissning med båda kända
    # varianterna än ingen expansion alls.
    unknown_query = expand_query("Vad var vinstmarginalen 2023?", None)
    assert "årets resultat" in unknown_query.lower()
    assert "periodens resultat" in unknown_query.lower()


def test_rrf_prefers_consensus_over_single_list():
    """En kandidat som båda rankarna är hyfsat överens om ska slå en som
    bara EN rankare älskar - det är hela poängen med fusionen."""
    a = ["x", "consensus", "y"]
    b = ["z", "consensus", "w"]
    assert _rrf_fuse([a, b])[0] == "consensus"


def test_rrf_is_deterministic_regardless_of_input_order():
    """Samma kandidater ska ge samma ordning oavsett i vilken ordning
    dellistorna kommer in. Här får 'p' och 'r' identisk poäng, så utan ett
    stabilt tiebreak avgjordes ordningen av dict-insättningsordningen."""
    a, b = ["p", "q", "r"], ["r", "q", "p"]
    assert _rrf_fuse([a, b]) == _rrf_fuse([b, a])


# --- retrieval-kvalitet (kräver byggt index) ---------------------------

@pytest.fixture(scope="module")
def searcher():
    if not CHROMA_DIR.exists() or not any(CHUNKS_DIR.glob("*.json")):
        pytest.skip("index saknas - kör 'python -m src.chunking' och "
                    "'python -m src.vectorstore' först")
    return HybridSearcher(CHROMA_DIR, CHUNKS_DIR)


@pytest.fixture(scope="module")
def ranks(searcher):
    out = []
    for query, accepted in GROUND_TRUTH:
        results = searcher.search(query, top_k=20)
        rank = next((i for i, r in enumerate(results, 1) if r.chunk_id in accepted), None)
        out.append((query, rank))
    return out


def test_every_ground_truth_answer_is_retrieved(ranks):
    missing = [q for q, r in ranks if r is None]
    assert not missing, f"facit-svaret hittades inte alls för: {missing}"


def test_recall_at_5_is_complete(ranks):
    """Alla facit-svar ska rymmas i topp 5. Det är den kritiska gränsen:
    chunkar utanför topp-k når aldrig språkmodellen i senare faser, och
    frågan blir då obesvarbar oavsett hur bra svaret formuleras."""
    outside = [(q, r) for q, r in ranks if r is None or r > 5]
    assert not outside, f"facit-svar utanför topp 5: {outside}"


def test_filters_do_not_exclude_the_answer(searcher):
    """Metadatafiltret får aldrig filtrera bort det korrekta svaret -
    ett för aggressivt filter är värre än inget filter."""
    for query, accepted in GROUND_TRUTH:
        ids = {r.chunk_id for r in searcher.search(query, top_k=20)}
        assert ids & accepted, f"filtret uteslöt svaret för: {query!r}"


def test_search_falls_back_when_filter_matches_nothing(searcher):
    """En fråga om ett bolag/år som inte finns ska ändå ge träffar, inte
    ett tomt resultat."""
    results = searcher.search("Vad var Hexatronics omsättning 1998?", top_k=5)
    assert results, "sökningen gav inga träffar alls"


def test_results_carry_citation_metadata(searcher):
    for r in searcher.search("Vad var Volvos summa tillgångar 2023?", top_k=5):
        assert r.document and r.company and r.fiscal_year
        assert r.pages and all(isinstance(p, int) for p in r.pages)
        assert r.text.strip()


def test_chunk_ids_are_unique_in_results(searcher):
    """Fusionen får inte returnera samma chunk två gånger."""
    ids = [r.chunk_id for r in searcher.search("Volvos rörelseresultat 2024", top_k=10)]
    assert len(ids) == len(set(ids))


def test_ratio_query_retrieves_both_underlying_facts(searcher):
    """Ett nyckeltal som ska BERÄKNAS (docs/SCOPE.md) kräver att BÅDA
    ingående poster finns i samma kontext. "Rörelsemarginal" står inte
    ordagrant i någon rad - bara "Rörelseresultat" och "Nettoomsättning"
    var för sig gör det. Utan query-expansion hittades bara den ena
    posten även vid top_k=20 (se docs/DECISIONS_FAS4.md)."""
    results = searcher.search("Vad var Hexatronics rörelsemarginal 2023?", top_k=8)
    ids = {r.chunk_id for r in results}
    needed = {
        "hexatronic_2023.pdf::resultaträkning::rad0",  # Nettoomsättning
        "hexatronic_2023.pdf::resultaträkning::rad10",  # Rörelseresultat
    }
    assert needed <= ids, f"saknar: {needed - ids}"


def test_multi_year_query_covers_all_mentioned_years(searcher):
    """En flerårsfråga (prioritet 2 i docs/SCOPE.md) får inte tystats ner
    till bara det först nämnda året - resultaten ska spänna över minst två
    olika dokument (olika räkenskapsår) för samma bolag."""
    results = searcher.search(
        "Hur har Volvos rörelseresultat förändrats från 2023 till 2024?", top_k=10
    )
    documents = {r.document for r in results if r.company == "volvo"}
    assert len({"volvo_2023.pdf", "volvo_2024.pdf"} & documents) == 2, (
        f"täcker inte båda räkenskapsåren, dokument i träffarna: {documents}"
    )
