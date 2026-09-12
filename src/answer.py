"""Kopplar samman Fas 3:s retrieval med Fas 4:s prompt och LLM-leverantör
till en fråga-in, svar-med-källa-ut-funktion.
"""

import time
from dataclasses import dataclass

from .hybrid_search import HybridSearcher
from .llm import LLMProvider
from .progress import ProgressCallback, emit
from .prompts import NO_ANSWER_PHRASE, build_prompt
from .search import SearchResult
from .segment_check import check_segment_consistency


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

    # Promptstorleken är värd att visa: den förklarar både kostnaden och
    # varför LLM-steget dominerar tidsåtgången.
    prompt_chars = len(prompt.system) + len(prompt.user)
    emit(
        on_progress,
        "llm",
        "Skickar underlaget till DeepSeek",
        f"{prompt_chars:,} tecken kontext".replace(",", " "),
        {"prompt_chars": prompt_chars},
    )

    started = time.perf_counter()
    text = provider.complete(prompt.system, prompt.user)
    llm_ms = (time.perf_counter() - started) * 1000
    emit(
        on_progress,
        "done",
        "Svar genererat",
        f"{len(text)} tecken",
        {"elapsed_ms": llm_ms},
    )

    is_no_answer = text.strip().startswith(NO_ANSWER_PHRASE)
    # Efterhandskontroll segment vs. koncern (fixar Y2, docs/evaluation.md,
    # se src/segment_check.py för fullständig motivering). Körs bara när
    # modellen faktiskt gav ett svar - "Jag hittar inte svaret" har inget
    # att flagga. Läser de FAKTISKA källorna, inte prompten, så kontrollen
    # fångar retrieval-fel som en promptregel inte kan skydda mot.
    if not is_no_answer:
        warning = check_segment_consistency(question, text, results)
        if warning:
            emit(on_progress, "segment_check", "Flaggade segment/koncern-osäkerhet", warning)
            text = f"{text}\n\n{warning}"

    return Answer(
        question=question,
        text=text,
        sources=prompt.sources,
        is_no_answer=is_no_answer,
    )
