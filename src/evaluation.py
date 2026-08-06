"""Utvärderingsharness (Fas 5): kör facit-frågorna genom hela pipelinen
(retrieval + svarsgenerering) och loggar fråga, svar, källhänvisning och
råa retrieval-träffar för varje.

Bedömningen (rätt/fel/delvis) görs INTE automatiskt här - att jämföra
fritextsvar mot facit kräver läsning, inte strängmatchning (se
docs/evaluation.md för resonemang). Harnessen producerar underlaget för
den bedömningen: en strukturerad logg per fråga, sparad som JSON, som
sedan granskas och sammanfattas i docs/evaluation.md.
"""

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .answer import answer_question
from .hybrid_search import HybridSearcher
from .llm import LLMProvider


@dataclass
class EvalCase:
    id: str
    question_type: str  # "enårsuppslag" | "yoy" | "nyckeltal" | "kvalitativ"
    question: str
    expected: str  # facit i klartext, inkl. källhänvisning där relevant
    notes: str = ""


# Facit godkänt av användaren 2026-08-06 efter korsverifiering mot rå
# PDF-text (oberoende av vår egen extraktionspipeline) - se docs/evaluation.md.
EVAL_SET: list[EvalCase] = [
    EvalCase("E1", "enårsuppslag", "Vad var Volvos nettoomsättning 2024?",
             "526.816 Mkr (volvo_2024.pdf, s. 37, Volvokoncernen)"),
    EvalCase("E2", "enårsuppslag", "Vad var Volvos summa tillgångar 2023?",
             "674.068 Mkr (volvo_2023.pdf, s. 62, Volvokoncernen)"),
    EvalCase("E3", "enårsuppslag", "Vad var Hexatronics nettoomsättning 2023?",
             "8 150 MSEK (hexatronic_2023.pdf, s. 86)"),
    EvalCase("E4", "enårsuppslag", "Vad var Hexatronics summa tillgångar 2025?",
             "8 057 MSEK (hexatronic_2025.pdf, s. 113)"),
    EvalCase("E5", "enårsuppslag", "Vad var SkiStars nettoomsättning 2023/24?",
             "4 679 385 TSEK (skistar_2023-24.pdf, s. 104, koncernen)"),
    EvalCase("E6", "enårsuppslag", "Vad var SkiStars summa tillgångar 2024/25?",
             "8 762 467 TSEK (skistar_2024-25.pdf, s. 120, koncernen)"),
    EvalCase("Y1", "yoy", "Hur har Hexatronics nettoomsättning utvecklats 2023-2025?",
             "8 150 (2023) -> 7 581 (2024) -> 7 519 (2025) MSEK, minskande. "
             "(hexatronic_2023.pdf s.86, hexatronic_2024.pdf s.100, hexatronic_2025.pdf s.112)"),
    EvalCase("Y2", "yoy", "Hur har Volvos rörelseresultat förändrats från 2023 till 2024?",
             "66.784 (2023) -> 66.611 (2024) Mkr, i det närmaste oförändrat. "
             "(volvo_2023.pdf s.59, volvo_2024.pdf s.37, Volvokoncernen)"),
    EvalCase("Y3", "yoy", "Hur har SkiStars årets resultat utvecklats från 2023/24 till 2024/25?",
             "472 887 (2023/24) -> 552 019 (2024/25) TSEK, ökande. "
             "(skistar_2023-24.pdf s.104, skistar_2024-25.pdf s.119)"),
    EvalCase("Y4", "yoy", "Hur har Volvos eget kapital förändrats från 2023 till 2024?",
             "177.791 (2023) -> 194.049 (2024) Mkr, ökande. "
             "(volvo_2023.pdf s.63, volvo_2024.pdf s.41, Volvokoncernen)"),
    EvalCase("Y5", "yoy", "Hur har Hexatronics årets resultat förändrats från 2023 till 2024?",
             "846 (2023) -> 344 (2024) MSEK, kraftig minskning. "
             "(hexatronic_2023.pdf s.86, hexatronic_2024.pdf s.100)"),
    EvalCase("N1", "nyckeltal", "Vad var Hexatronics rörelsemarginal 2023?",
             "ca 13,8 % (1 122 / 8 150, hexatronic_2023.pdf s. 86)"),
    EvalCase("N2", "nyckeltal", "Vad var Volvos soliditet 2023?",
             "ca 26,8 % (180.739 / 674.068, volvo_2023.pdf s. 62-63, Volvokoncernen; "
             "bolaget anger även själv 26,8% i löptext)"),
    EvalCase("N3", "nyckeltal", "Vad var SkiStars soliditet 2023/24?",
             "ca 42,1 % (3 656 803 / 8 681 892, skistar_2023-24.pdf s. 105)"),
    EvalCase("N4", "nyckeltal", "Vad var Hexatronics vinstmarginal 2024?",
             "ca 4,5 % (344 / 7 581, hexatronic_2024.pdf s. 100)"),
    EvalCase("N5", "nyckeltal", "Vad var Volvos vinstmarginal 2023?",
             "ca 9,0 % (49.932 / 552.764, volvo_2023.pdf s. 59, Volvokoncernen)"),
    EvalCase("K1", "kvalitativ", "Vilka riskkategorier lyfter Hexatronic fram i sin förvaltningsberättelse?",
             "Öppet facit - bedöms mot dokumentets faktiska riskavsnitt vid granskning."),
    EvalCase("K2", "kvalitativ", "Vilka risker eller osäkerhetsfaktorer nämner Volvo i sin förvaltningsberättelse?",
             "Öppet facit - bedöms mot dokumentets faktiska riskavsnitt vid granskning."),
]


def run_evaluation(searcher: HybridSearcher, provider: LLMProvider) -> list[dict]:
    results = []
    for case in EVAL_SET:
        answer = answer_question(case.question, searcher, provider)
        results.append({
            "id": case.id,
            "question_type": case.question_type,
            "question": case.question,
            "expected": case.expected,
            "generated_answer": answer.text,
            "is_no_answer": answer.is_no_answer,
            "sources": [
                {
                    "document": s.document,
                    "pages": s.pages,
                    "section": s.section,
                    "chunk_type": s.chunk_type,
                }
                for s in answer.sources
            ],
        })
    return results


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    searcher = HybridSearcher(root / "data" / "chroma", root / "data" / "chunks")
    from .llm import get_provider

    provider = get_provider("deepseek")
    results = run_evaluation(searcher, provider)
    out_path = root / "data" / "evaluation_run.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Körde {len(results)} frågor, resultat sparat i {out_path}")
