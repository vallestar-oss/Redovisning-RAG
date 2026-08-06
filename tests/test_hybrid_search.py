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

from src.hybrid_search import HybridSearcher, _rrf_fuse, parse_query_filters

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
    "query, company, fiscal_year",
    [
        ("Vad var Hexatronics nettoomsättning 2023?", "hexatronic", "2023"),
        ("Vad var Volvos rörelseresultat 2024?", "volvo", "2024"),
        ("Volvokoncernens eget kapital 2023", "volvo", "2023"),
        ("Vad var SkiStars nettoomsättning 2023/24?", "skistar", "2023-24"),
        # Ensamt årtal för bolag med brutet räkenskapsår ska tolkas som det
        # räkenskapsår som SLUTAR det året, inte som ett kalenderår som inte
        # finns för bolaget.
        ("SkiStars nettoomsättning 2024", "skistar", "2023-24"),
        ("SkiStars summa tillgångar 2025", "skistar", "2024-25"),
        # Årtal utanför materialet ska inte ge ett årsfilter alls.
        ("SkiStars omsättning 2019", "skistar", None),
        # Ingen entydig signal alls -> inget filter.
        ("Hur har soliditeten utvecklats?", None, None),
    ],
)
def test_parse_query_filters(query, company, fiscal_year):
    filters = parse_query_filters(query, COMPANY_YEARS)
    assert filters.company == company
    assert filters.fiscal_year == fiscal_year


def test_filters_build_valid_chroma_where():
    both = parse_query_filters("Volvos omsättning 2024", COMPANY_YEARS)
    assert both.as_chroma_where() == {
        "$and": [{"company": "volvo"}, {"fiscal_year": "2024"}]
    }
    only_company = parse_query_filters("Volvos omsättning", COMPANY_YEARS)
    assert only_company.as_chroma_where() == {"company": "volvo"}
    assert parse_query_filters("omsättning", COMPANY_YEARS).as_chroma_where() is None


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
