"""Extraherar ren text per sida ur årsredovisnings-PDF:er."""

from dataclasses import dataclass
from pathlib import Path

import pdfplumber


@dataclass
class PageText:
    document: str
    page_number: int
    text: str


def extract_pages(pdf_path: Path) -> list[PageText]:
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            pages.append(PageText(document=pdf_path.name, page_number=i, text=text))
    return pages
