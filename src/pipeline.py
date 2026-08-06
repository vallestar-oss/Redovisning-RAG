"""Kör hela Fas 1-extraktionen för ett dokument och sparar strukturerat som JSON.

Varje sida i dokumentet sparas med sidnummer och ren text (från
`extraction.extract_pages`). Sidor som `locate_statements.locate_statement_pages`
identifierar som en huvudräkning (resultat-/balans-/kassaflödesräkning) får
dessutom `statement_type` och tabelldata (`table_rows`, från
`tables.extract_financial_rows`) - separat identifierbart från den
löpande texten enligt Fas 1-kravet.

Bolagsnamn och räkenskapsår hämtas ur filnamnet, som följer konventionen
`bolag_år.pdf` (se docs/DECISIONS.md om filnamnsbyten i Fas 0).
"""

import json
from pathlib import Path

from .extraction import extract_pages
from .locate_statements import locate_statement_pages
from .tables import extract_financial_rows

import pdfplumber

# SkiStars koncernsidor har en väsentligt rörigare layout (tabeller sida vid
# sida med avvikande underrubriker, inbäddat diagram) som ger fel
# extraktion - se docs/DECISIONS.md. Beslut: använd moderbolagets räkningar
# för SkiStar, koncernens för övriga bolag.
_LEVEL_BY_COMPANY_PREFIX = {
    "skistar": "moderbolag",
}
_DEFAULT_LEVEL = "koncern"


def _level_for(pdf_path: Path) -> str:
    prefix = pdf_path.stem.split("_")[0].lower()
    return _LEVEL_BY_COMPANY_PREFIX.get(prefix, _DEFAULT_LEVEL)


def _company_and_year(pdf_path: Path) -> tuple[str, str]:
    stem = pdf_path.stem
    company, _, year = stem.partition("_")
    return company, year


def process_document(pdf_path: Path) -> dict:
    level = _level_for(pdf_path)
    company, year = _company_and_year(pdf_path)

    statement_pages = locate_statement_pages(pdf_path, level=level)
    page_to_statement_type = {}
    for statement_type, pages in [
        ("resultaträkning", statement_pages.resultaträkning),
        ("balansräkning", statement_pages.balansräkning),
        ("kassaflödesanalys", statement_pages.kassaflödesanalys),
    ]:
        for p in pages:
            page_to_statement_type[p] = statement_type

    pages_data = []
    with pdfplumber.open(pdf_path) as pdf:
        raw_pages = extract_pages(pdf_path)
        for i, page_text in enumerate(raw_pages, start=1):
            entry = {"page": i, "text": page_text.text}
            statement_type = page_to_statement_type.get(i)
            if statement_type is not None:
                rows = extract_financial_rows(pdf.pages[i - 1])
                entry["statement_type"] = statement_type
                entry["table_rows"] = [
                    {"label": r.label, "values": r.values} for r in rows
                ]
            pages_data.append(entry)

    return {
        "document": pdf_path.name,
        "company": company,
        "fiscal_year": year,
        "statement_level": level,
        "statement_pages": {
            "resultaträkning": statement_pages.resultaträkning,
            "balansräkning": statement_pages.balansräkning,
            "kassaflödesanalys": statement_pages.kassaflödesanalys,
        },
        "pages": pages_data,
    }


def process_all(raw_dir: Path, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for pdf_path in sorted(raw_dir.glob("*.pdf")):
        data = process_document(pdf_path)
        out_path = out_dir / f"{pdf_path.stem}.json"
        out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(out_path)
    return written


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    result = process_all(root / "data" / "raw", root / "data" / "processed")
    for p in result:
        print(p)
