"""Tester för answer_question() - kopplingen mellan retrieval, LLM och den
nya segment/koncern-efterhandskontrollen (src/segment_check.py, fixar Y2).

Använder fejkade Searcher/Provider (samma mönster som LLMProvider-
protokollet i src/llm.py är byggt för att stödja) istället för ett riktigt
index/embeddingmodell eller ett riktigt LLM-anrop.
"""

from src.answer import answer_question
from src.search import SearchResult


class _FakeSearcher:
    def __init__(self, results: list[SearchResult]):
        self._results = results

    def search(self, question, top_k=8, on_progress=None):
        return self._results


class _FakeProvider:
    def __init__(self, response: str):
        self._response = response

    def complete(self, system, user):
        return self._response


def _volvo_source(text: str, chunk_type: str = "fact") -> SearchResult:
    return SearchResult(
        chunk_id="volvo_2024.pdf::resultaträkning::rad9",
        document="volvo_2024.pdf",
        company="volvo",
        fiscal_year="2024",
        section="resultaträkning",
        chunk_type=chunk_type,
        pages=[37],
        text=text,
        distance=float("nan"),
    )


def test_answer_gets_segment_warning_appended_when_sources_do_not_confirm_group_level():
    sources = [
        _volvo_source(
            "Volvo resultaträkning 2024: Rörelseresultat var 62.198 (Industriverksamheten 2024)."
        )
    ]
    fake_answer = "Volvokoncernens rörelseresultat 2024 var 62.198 Mkr (volvo_2024.pdf, s. 37)."
    answer = answer_question(
        "Vad var Volvos rörelseresultat 2024?",
        _FakeSearcher(sources),
        _FakeProvider(fake_answer),
    )
    assert answer.text.startswith(fake_answer)
    assert "OBS (automatisk efterhandskontroll)" in answer.text
    assert not answer.is_no_answer


def test_answer_unchanged_when_group_level_confirmed():
    sources = [
        _volvo_source(
            "Volvo resultaträkning 2024: Rörelseresultat var 62.198 (Industriverksamheten 2024), "
            "66.611 (Volvokoncernen 2024)."
        )
    ]
    fake_answer = "Volvokoncernens rörelseresultat 2024 var 66.611 Mkr (volvo_2024.pdf, s. 37)."
    answer = answer_question(
        "Vad var Volvos rörelseresultat 2024?",
        _FakeSearcher(sources),
        _FakeProvider(fake_answer),
    )
    assert answer.text == fake_answer


def test_answer_unaffected_when_not_a_segmented_company():
    sources = [
        SearchResult(
            chunk_id="hexatronic_2023.pdf::resultaträkning::rad0",
            document="hexatronic_2023.pdf",
            company="hexatronic",
            fiscal_year="2023",
            section="resultaträkning",
            chunk_type="fact",
            pages=[86],
            text="Hexatronic resultaträkning 2023: Nettoomsättning var 8 150 (2023).",
            distance=float("nan"),
        )
    ]
    fake_answer = "Hexatronics nettoomsättning 2023 var 8 150 MSEK (hexatronic_2023.pdf, s. 86)."
    answer = answer_question(
        "Vad var Hexatronics nettoomsättning 2023?",
        _FakeSearcher(sources),
        _FakeProvider(fake_answer),
    )
    assert answer.text == fake_answer


def test_no_segment_check_when_model_gives_up():
    """"Jag hittar inte svaret" har inget att flagga - kontrollen ska inte
    köras alls, så den lägger inte till en förvirrande extra rad."""
    from src.prompts import NO_ANSWER_PHRASE

    sources = [
        _volvo_source(
            "Volvo resultaträkning 2024: Rörelseresultat var 62.198 (Industriverksamheten 2024)."
        )
    ]
    fake_answer = f"{NO_ANSWER_PHRASE} Ingen koncernsiffra fanns i underlaget."
    answer = answer_question(
        "Vad var Volvos rörelseresultat 2024?",
        _FakeSearcher(sources),
        _FakeProvider(fake_answer),
    )
    assert answer.text == fake_answer
    assert answer.is_no_answer
