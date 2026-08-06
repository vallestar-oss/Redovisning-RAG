"""Strukturella tester för utvärderingsharnessen (Fas 5).

Bedömningen rätt/delvis/fel görs INTE här - att jämföra fritextsvar mot
facit kräver läsning, inte strängmatchning (se docs/evaluation.md för
konkreta exempel på varför: "rätt tal, fel post" och "rätt tal, fel sida"
skulle båda klarat ett naivt textmatchningstest). Dessa tester säkerställer
istället att harnessen själv fungerar korrekt - att alla frågor körs, att
varje svar har källhänvisning, och att facit-listan är välformad.
"""

from pathlib import Path

import pytest

from src.evaluation import EVAL_SET

ROOT = Path(__file__).resolve().parent.parent


def test_eval_set_covers_all_priority_question_types():
    types = {c.question_type for c in EVAL_SET}
    assert types == {"enårsuppslag", "yoy", "nyckeltal", "kvalitativ"}, (
        f"facit täcker inte alla fyra frågetyper i docs/SCOPE.md, hittade: {types}"
    )


def test_eval_set_has_unique_ids():
    ids = [c.id for c in EVAL_SET]
    assert len(ids) == len(set(ids))


def test_eval_set_questions_and_expected_are_non_empty():
    for c in EVAL_SET:
        assert c.question.strip()
        assert c.expected.strip()


@pytest.mark.skipif(
    not (ROOT / "data" / "chroma").exists(),
    reason="index saknas - kör 'python -m src.chunking' och 'python -m src.vectorstore' först",
)
def test_evaluation_run_produces_one_result_per_case():
    """Kör HELA facit-setet genom pipelinen en gång och verifierar att
    varje fråga gav ett svar med minst en källhänvisning - inte att svaret
    är korrekt (det kräver läsning, se docs/evaluation.md)."""
    import os

    if not os.environ.get("DEEPSEEK_API_KEY"):
        pytest.skip("DEEPSEEK_API_KEY saknas - kan inte köra mot LLM:et")

    from src.evaluation import run_evaluation
    from src.hybrid_search import HybridSearcher
    from src.llm import get_provider

    searcher = HybridSearcher(ROOT / "data" / "chroma", ROOT / "data" / "chunks")
    provider = get_provider("deepseek")
    results = run_evaluation(searcher, provider)

    assert len(results) == len(EVAL_SET)
    for r in results:
        assert r["generated_answer"].strip(), f"{r['id']}: tomt svar"
        if not r["is_no_answer"]:
            assert r["sources"], f"{r['id']}: svar utan källhänvisning"
