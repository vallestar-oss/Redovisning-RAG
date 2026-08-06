"""Extraherar ren text per sida ur årsredovisnings-PDF:er.

Spaltmedveten: `page.extract_text()` läser ord i y-position-ordning över
HELA sidans bredd, vilket i en tvåspaltig layout (vanligt i förvaltnings-
berättelsens narrativa sidor) vävar ihop två orelaterade meningar från
vänster och höger spalt rad för rad - se docs/DECISIONS_FAS2.md. Denna
modul upptäcker istället en eventuell "gata" (ett vertikalt tomrum som
inget ord korsar) i sidans mittparti och läser i så fall vänster spalt
i sin helhet, sedan höger spalt i sin helhet. Sidor utan en sådan tydlig
gata (de flesta försätts-/tabellsidor, enkolumnstext) faller tillbaka på
`extract_text()` som tidigare.
"""

from dataclasses import dataclass
from pathlib import Path

import pdfplumber

_GUTTER_SCAN_LOW_FRACTION = 0.30  # sök en gata endast i sidans mittparti (30-70% av bredden) -
_GUTTER_SCAN_HIGH_FRACTION = 0.70  # ...en gata nära kanten vore knappast en spaltdelning
_MIN_GUTTER_WIDTH = 10.0  # pt - smalare tomrum är bara normalt mellanrum, inte en spaltgata
_LINE_Y_TOL = 2.0
# Rubriker/underrubriker har normalt en radhöjd (bottom-top) klart över
# brödtextens ~10pt (vi observerade 18-33pt för rubriktext mot 10pt för
# brödtext) och spänner ofta medvetet över hela sidbredden som ett
# designelement - de ska INTE räknas som att de blockerar en spaltgata,
# annars hittas aldrig gatan på sidor med en rubrik ovanför den tvåspaltiga
# brödtexten.
_MAX_BODY_TEXT_HEIGHT = 14.0


@dataclass
class PageText:
    document: str
    page_number: int
    text: str


def _find_column_gutter(words: list[dict], page_width: float) -> float | None:
    """Hittar x-positionen för en eventuell tvåspaltsgata: det bredaste
    sammanhängande tomrummet i sidans mittparti som inget ord korsar."""
    lo = page_width * _GUTTER_SCAN_LOW_FRACTION
    hi = page_width * _GUTTER_SCAN_HIGH_FRACTION
    body_words = [w for w in words if (w["bottom"] - w["top"]) <= _MAX_BODY_TEXT_HEIGHT]
    intervals = sorted(
        (max(w["x0"], lo), min(w["x1"], hi)) for w in body_words if w["x1"] > lo and w["x0"] < hi
    )
    if not intervals:
        return None

    merged: list[list[float]] = []
    for x0, x1 in intervals:
        if merged and x0 <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], x1)
        else:
            merged.append([x0, x1])

    gaps = []
    prev_end = lo
    for start, end in merged:
        if start - prev_end > 0:
            gaps.append((prev_end, start))
        prev_end = end
    if hi - prev_end > 0:
        gaps.append((prev_end, hi))

    if not gaps:
        return None
    widest = max(gaps, key=lambda g: g[1] - g[0])
    if widest[1] - widest[0] < _MIN_GUTTER_WIDTH:
        return None
    return (widest[0] + widest[1]) / 2


def _words_to_text(words: list[dict]) -> str:
    """Grupperar ord till rader (y-position) och rader till text, sorterat
    uppifrån och ned, vänster till höger inom varje rad."""
    lines: list[tuple[float, list[dict]]] = []
    for w in sorted(words, key=lambda w: w["top"]):
        if lines and abs(lines[-1][0] - w["top"]) <= _LINE_Y_TOL:
            lines[-1][1].append(w)
        else:
            lines.append((w["top"], [w]))
    out_lines = []
    for _, line_words in lines:
        line_words.sort(key=lambda w: w["x0"])
        out_lines.append(" ".join(w["text"] for w in line_words))
    return "\n".join(out_lines)


def _extract_page_text(page: pdfplumber.page.Page) -> str:
    words = page.extract_words()
    if not words:
        return page.extract_text() or ""

    gutter = _find_column_gutter(words, page.width)
    if gutter is None:
        return page.extract_text() or ""

    # Ord som korsar gatan (t.ex. en rubrik som spänner över hela
    # sidbredden - se _MAX_BODY_TEXT_HEIGHT) tilldelas efter sin
    # MITTPUNKT, så att inget ord faller bort ur båda buckets.
    left = [w for w in words if (w["x0"] + w["x1"]) / 2 < gutter]
    right = [w for w in words if (w["x0"] + w["x1"]) / 2 >= gutter]
    if not left or not right:
        # Ingen av "spalterna" har faktiskt innehåll - ingen riktig
        # tvåspaltslayout, bara en slumpmässig gata. Använd standardläget.
        return page.extract_text() or ""

    return _words_to_text(left) + "\n" + _words_to_text(right)


def extract_pages(pdf_path: Path) -> list[PageText]:
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = _extract_page_text(page)
            pages.append(PageText(document=pdf_path.name, page_number=i, text=text))
    return pages
