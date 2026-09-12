"""Tester för segment/koncern-efterhandskontrollen (Fas 6, fixar Y2).

Se src/segment_check.py för den fullständiga motiveringen. Testerna kräver
inget index/embeddingmodell - de går direkt på SearchResult-objekt (delvis
byggda från riktig data i data/chunks/, för att förankra testet i det
faktiska Y2-fallet: volvo_2024.pdf::p210::1 är exakt den text-chunk som
felaktigt citerades som koncernen i baseline, se docs/evaluation.md).
"""

import json
from pathlib import Path

from src.search import SearchResult
from src.segment_check import SEGMENT_WARNING, _has_confirmed_group_level_value, check_segment_consistency

ROOT = Path(__file__).resolve().parent.parent
CHUNKS_DIR = ROOT / "data" / "chunks"


def _result(text: str, chunk_type: str = "fact", chunk_id: str = "test::0") -> SearchResult:
    return SearchResult(
        chunk_id=chunk_id,
        document="volvo_2024.pdf",
        company="volvo",
        fiscal_year="2024",
        section="resultaträkning",
        chunk_type=chunk_type,
        pages=[37],
        text=text,
        distance=float("nan"),
    )


def _real_chunk(document: str, chunk_id: str) -> SearchResult:
    """Läser en verklig chunk ur data/chunks/ - inget index/embedding krävs."""
    chunks = json.loads((CHUNKS_DIR / document).read_text(encoding="utf-8"))
    match = next(c for c in chunks if c["chunk_id"] == chunk_id)
    return SearchResult(
        chunk_id=match["chunk_id"],
        document=match["document"],
        company=match["company"],
        fiscal_year=match["fiscal_year"],
        section=match["section"] or "",
        chunk_type=match["chunk_type"],
        pages=list(match["pages"]),
        text=match["text"],
        distance=float("nan"),
    )


def test_no_warning_when_no_source_mentions_a_segment():
    """Hexatronic/SkiStar redovisar aldrig per segment - inget att flagga."""
    sources = [_result("Hexatronic resultaträkning 2024: Rörelseresultat var 344 (2024), 846 (2023).")]
    assert check_segment_consistency(
        "Vad var Hexatronics rörelseresultat 2024?", "Rörelseresultatet var 344 MSEK (2024).", sources
    ) is None


def test_no_warning_when_a_confirmed_group_level_source_exists():
    """En strukturerad (fact) chunk med tillförlitligt taggad
    Volvokoncernen-siffra räcker för att bekräfta koncernnivå, även om
    ANDRA källor i samma svar bara nämner enskilda segment."""
    sources = [
        _result(
            "Volvo resultaträkning 2024: Rörelseresultat var 62.198 (Industriverksamheten 2024), "
            "4.042 (Financial Services 2024), 371 (Elimineringar 2024), 66.611 (Volvokoncernen 2024).",
            chunk_type="fact",
        )
    ]
    answer = "Volvokoncernens rörelseresultat 2024 var 66.611 Mkr (volvo_2024.pdf, s. 37)."
    assert check_segment_consistency("Vad var Volvos rörelseresultat 2024?", answer, sources) is None


def test_warns_when_answer_silently_assumes_group_level():
    """Källorna är rena segmentsiffror (ingen Volvokoncernen-tagg alls) och
    svaret nämner varken segment eller koncern explicit - regel 2 i
    uppgiften: svaret ska säga det tydligt istället för att anta koncern."""
    sources = [
        _result(
            "Volvo resultaträkning 2024: Rörelseresultat var 62.198 (Industriverksamheten 2024), "
            "63.063 (Industriverksamheten 2023).",
            chunk_type="fact",
        )
    ]
    answer = "Volvos rörelseresultat var 62.198 Mkr (2024) jämfört med 63.063 Mkr (2023)."
    result = check_segment_consistency("Vad var Volvos rörelseresultat 2024?", answer, sources)
    assert result == SEGMENT_WARNING


def test_warns_when_answer_falsely_claims_group_level():
    """Speglar det faktiska Y2-felet: svaret påstår "Volvokoncernen" trots
    att ingen källa har en tillförlitlig koncernsiffra - regel 1 i
    uppgiften: en mismatch mellan påstående och källa ska flaggas."""
    sources = [
        _result(
            "Volvo resultaträkning 2024: Rörelseresultat var 62.198 (Industriverksamheten 2024), "
            "63.063 (Industriverksamheten 2023).",
            chunk_type="fact",
        )
    ]
    answer = "Volvokoncernens rapporterade rörelseresultat uppgick till 62.198 Mkr (2024)."
    result = check_segment_consistency("Vad var Volvos rörelseresultat 2024?", answer, sources)
    assert result == SEGMENT_WARNING


def test_no_warning_when_answer_already_names_the_segment_transparently():
    """Om svaret redan själv är transparent om att det avser ett segment
    (utan att också påstå "Volvokoncernen"/"koncernen") har det redan gjort
    vad promptregel 7 kräver - inget mer att flagga."""
    sources = [
        _result(
            "Volvo resultaträkning 2024: Rörelseresultat var 62.198 (Industriverksamheten 2024).",
            chunk_type="fact",
        )
    ]
    answer = "Industriverksamhetens rörelseresultat var 62.198 Mkr (2024)."
    assert check_segment_consistency("Vad var Volvos rörelseresultat 2024?", answer, sources) is None


def test_no_warning_when_question_asks_about_a_specific_segment():
    """Frågan efterfrågar uttryckligen ett segment - då förväntas ingen
    koncernsiffra, och kontrollen ska inte flagga något."""
    sources = [
        _result(
            "Volvo resultaträkning 2024: Rörelseresultat var 4.042 (Financial Services 2024).",
            chunk_type="fact",
        )
    ]
    answer = "Financial Services rörelseresultat var 4.042 Mkr (2024)."
    result = check_segment_consistency(
        "Vad var Volvos rörelseresultat för Financial Services 2024?", answer, sources
    )
    assert result is None


def test_unstructured_text_chunk_is_not_trusted_for_group_level_confirmation():
    """"text"-chunkar (oformaterad löptext) saknar tillförlitlig
    "(Segment år)"-taggning och kan innehålla sidhuvud-boilerplate
    ("VOLVOKONCERNEN 2024" som sidrubrik, inte en datataggning) - de får
    därför INTE räknas som en bekräftad koncernsiffra, bara som en lös
    signal om att frågan rör ett segmenterat bolag."""
    sources = [
        _result(
            "207\nVOLVOKONCERNEN 2024\nÖVRIG INFORMATION\nIndustriverksamheten\n"
            "Mkr 2024 2023\nRörelseresultat 62.198 63.063",
            chunk_type="text",
        )
    ]
    answer = "Volvos rörelseresultat var 62.198 Mkr (2024)."
    result = check_segment_consistency("Vad var Volvos rörelseresultat 2024?", answer, sources)
    assert result == SEGMENT_WARNING


def test_regression_against_the_actual_y2_source_chunk():
    """Grundad i den faktiska felkällan: volvo_2024.pdf::p210::1 är exakt
    den text-chunk som i baseline citerades som om den vore koncernen (se
    docs/evaluation.md, Y2). Den innehåller "Industriverksamheten" som
    genuin radetikett för Rörelseresultat-siffrorna, och nämner även ordet
    "Volvokoncernens" en gång - men bara i en orelaterad fotnotshänvisning
    ("...presenteras efter Volvokoncernens balansräkning"), INTE som en
    tillförlitlig "(Volvokoncernen år)"-tagg på själva Rörelseresultat-
    värdet. Det är precis den skillnaden _has_confirmed_group_level_value
    måste göra rätt på - chunk_type "text" litar vi aldrig på för det,
    oavsett vilka ord som råkar förekomma i löptexten."""
    real_source = _real_chunk("volvo_2024.json", "volvo_2024.pdf::p210::1")
    assert "Industriverksamheten" in real_source.text
    assert "Volvokoncernens" in real_source.text  # nämns, men bara i en orelaterad fotnot
    assert not _has_confirmed_group_level_value(real_source)

    baseline_answer = (
        "Volvokoncernens rapporterade rörelseresultat uppgick till 62.198 Mkr (2024) "
        "jämfört med 63.063 Mkr (2023)."
    )
    result = check_segment_consistency(
        "Hur har Volvos rörelseresultat förändrats från 2023 till 2024?",
        baseline_answer,
        [real_source],
    )
    assert result == SEGMENT_WARNING
