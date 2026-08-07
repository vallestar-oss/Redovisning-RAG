"""Kopplar samman Fas 3:s retrieval med Fas 4:s prompt och LLM-leverantör
till en fråga-in, svar-med-källa-ut-funktion.
"""

from dataclasses import dataclass

from .hybrid_search import HybridSearcher
from .llm import LLMProvider
from .progress import ProgressCallback, emit
from .prompts import NO_ANSWER_PHRASE, build_prompt
from .search import SearchResult


@dataclass
class Answer:
    question: str
    text: str
    sources: list[SearchResult]
    is_no_answer: bool


def answer_question(
    question: str,
    searcher: HybridSearcher,
    provider: LLMProvider,
    # Uppmätt minimum för att en tvåpostsberäkning (t.ex. rörelsemarginal =
    # rörelseresultat / nettoomsättning) ska få båda ingående posterna i
    # samma kontext - vid top_k=5 saknades en av dem, vid 8 fanns båda.
    # Se docs/DECISIONS_FAS4.md.
    top_k: int = 8,
    # Valfri statusrapportering (src/progress.py). None = tyst, vilket är vad
    # tester, CLI och src/evaluation.py använder.
    on_progress: ProgressCallback | None = None,
) -> Answer:
    results = searcher.search(question, top_k=top_k, on_progress=on_progress)
    prompt = build_prompt(question, results)
    emit(on_progress, "llm", "Skickar underlaget till DeepSeek")
    text = provider.complete(prompt.system, prompt.user)
    emit(on_progress, "done", "Svar genererat")
    return Answer(
        question=question,
        text=text,
        sources=prompt.sources,
        is_no_answer=text.strip().startswith(NO_ANSWER_PHRASE),
    )
