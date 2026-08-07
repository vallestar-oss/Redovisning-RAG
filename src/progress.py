"""Statusrapportering under en fråga, utan att koppla kärnlogiken till UI:t.

Pipelinen (retrieval + LLM) tar emot en valfri callback och anropar den när
ett steg påbörjas eller blir klart. Streamlit-appen kopplar in sin egen
`st.status`-uppdatering; tester, CLI och `src/evaluation.py` skickar inget
alls och märker ingen skillnad.

Kärnkoden importerar aldrig `streamlit` - samma modularitetsprincip som
`LLMProvider` i `src/llm.py`: gränssnittet är en funktionssignatur, inte ett
beroende till en viss frontend.
"""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class ProgressEvent:
    """Ett rapporterat pipelinesteg.

    `step` är en stabil, maskinläsbar nyckel (för tester och ev. ikonval i
    UI:t), `message` är texten som visas för användaren, `detail` är en
    valfri precisering i klartext, och `data` bär strukturerad nyttolast för
    steg som har mer att visa än en rad text - t.ex. rankningstabellen från
    hybridsökningen, där UI:t vill rendera siffrorna själv.

    `data` hålls som en vanlig dict med JSON-vänliga värden: kärnkoden ska
    inte behöva veta hur UI:t tänker presentera den.
    """

    step: str
    message: str
    detail: str | None = None
    data: dict | None = None

    def elapsed_ms(self) -> float | None:
        """Bekvämlighet för UI:t: tiden steget tog, om den mättes."""
        if self.data is None:
            return None
        return self.data.get("elapsed_ms")


ProgressCallback = Callable[[ProgressEvent], None]


def emit(
    on_progress: ProgressCallback | None,
    step: str,
    message: str,
    detail: str | None = None,
    data: dict | None = None,
) -> None:
    """Rapporterar ett steg om någon lyssnar. No-op när on_progress är None,
    så anropande kod slipper `if on_progress is not None`-brus på varje rad."""
    if on_progress is not None:
        on_progress(
            ProgressEvent(step=step, message=message, detail=detail, data=data)
        )
