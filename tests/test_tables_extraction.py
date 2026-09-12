"""Verifierar att tabellextraktionen ger rimligt resultat på SAMTLIGA
dokument i data/raw, inte bara det första vi testade på (Fas 1, steg 4).

Körs mot varje dokuments faktiska huvudräkningssidor, lokaliserade med
`locate_statement_pages` på rätt nivå per bolag - `_level_for()` i
src/pipeline.py avgör nivån, och samtliga tre bolag kör idag "koncern"
(se src/locate_statements.py för historiken om varför SkiStar tidigare
körde på "moderbolag" och varför det inte längre gäller).
"""

import re
from pathlib import Path

import pdfplumber
import pytest

from src.locate_statements import locate_statement_pages
from src.tables import extract_financial_rows

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

# Nivåvalet (koncern/moderbolag) importeras från pipelinen istället för att
# dupliceras här - annars kan testet och produktionskoden glida isär och
# testet råka verifiera en annan nivå än den som faktiskt indexeras.
from src.pipeline import _level_for  # noqa: E402


def _all_documents() -> list[Path]:
    docs = sorted(DATA_DIR.glob("*.pdf"))
    assert docs, f"inga PDF:er hittades i {DATA_DIR}"
    return docs


def _parse_swedish_number(text: str) -> int:
    """Tolkar svenska belopp ("674.068", "8 733", "4 613 193", "–36.046")
    som heltal. Endast avsett för summeringsrader (inga decimaler)."""
    cleaned = text.strip().replace("−", "-").replace("–", "-")
    cleaned = cleaned.replace(" ", "").replace(".", "").replace(",", "")
    return int(cleaned)


@pytest.fixture(scope="module", params=_all_documents(), ids=lambda p: p.name)
def document_rows(request):
    """Extraherar rader från samtliga huvudräkningssidor i ETT dokument."""
    pdf_path = request.param
    level = _level_for(pdf_path)
    statement_pages = locate_statement_pages(pdf_path, level=level)
    all_pages = sorted(
        set(
            statement_pages.resultaträkning
            + statement_pages.balansräkning
            + statement_pages.kassaflödesanalys
        )
    )
    assert all_pages, f"{pdf_path.name}: inga huvudräkningssidor hittades (nivå={level})"

    rows_by_page = {}
    with pdfplumber.open(pdf_path) as pdf:
        for page_num in all_pages:
            rows_by_page[page_num] = extract_financial_rows(pdf.pages[page_num - 1])

    return pdf_path, statement_pages, rows_by_page


def test_every_located_page_yields_rows(document_rows):
    """Varje sida som pekats ut som en huvudräkning ska ge minst en rad."""
    pdf_path, _, rows_by_page = document_rows
    for page_num, rows in rows_by_page.items():
        assert rows, f"{pdf_path.name} sida {page_num}: extraherade noll rader"


def test_no_suspicious_rows(document_rows):
    """Fångar de tre felklasser vi hittade och åtgärdade under Fas 1:
    tom/orimligt kort etikett, radetiketter som bara innehåller siffror
    (t.ex. läckt tabellinnehåll från fel kolumn/region), och rader utan
    några extraherade värden."""
    pdf_path, _, rows_by_page = document_rows
    for page_num, rows in rows_by_page.items():
        for row in rows:
            label = row.label.strip()
            assert len(label) >= 2, f"{pdf_path.name} sida {page_num}: tom/kort etikett {row!r}"
            assert row.present_values, f"{pdf_path.name} sida {page_num}: inga värden {row!r}"
            assert any(c.isalpha() for c in label), (
                f"{pdf_path.name} sida {page_num}: etikett utan bokstäver (troligen "
                f"läckt tabellinnehåll) {row!r}"
            )


def test_balance_sheet_balances(document_rows):
    """Facit-kontroll: koncernens/moderbolagets tillgångar ska vara lika
    med summan av eget kapital och skulder - annars är siffrorna fel,
    oavsett hur rimliga de ser ut rad för rad."""
    pdf_path, statement_pages, rows_by_page = document_rows
    if not statement_pages.balansräkning:
        pytest.skip(f"{pdf_path.name}: ingen balansräkningssida lokaliserad")

    assets_total = None
    liabilities_equity_total = None
    for page_num in statement_pages.balansräkning:
        for row in rows_by_page[page_num]:
            label_lower = row.label.strip().lower()
            if label_lower in ("summa tillgångar",):
                assets_total = _parse_swedish_number(row.present_values[0])
            elif label_lower in ("summa eget kapital och skulder",):
                liabilities_equity_total = _parse_swedish_number(row.present_values[0])

    if assets_total is None or liabilities_equity_total is None:
        pytest.skip(
            f"{pdf_path.name}: hittade inte båda summeringsraderna "
            f"(tillgångar={assets_total}, eget kapital och skulder={liabilities_equity_total})"
        )

    assert assets_total == liabilities_equity_total, (
        f"{pdf_path.name}: balansräkningen balanserar inte - "
        f"tillgångar={assets_total} != eget kapital och skulder={liabilities_equity_total}"
    )


def test_values_are_parseable_numbers(document_rows):
    """Varje extraherat värde ska gå att tolka som ett tal - annars har
    något annat än en siffra smugit sig in i värdeslistan."""
    pdf_path, _, rows_by_page = document_rows
    number_re = re.compile(r"^[−\-–]?\d[\d\s.,]*$")
    for page_num, rows in rows_by_page.items():
        for row in rows:
            for value in row.present_values:
                assert number_re.match(value), (
                    f"{pdf_path.name} sida {page_num}: värdet {value!r} i {row.label!r} "
                    f"ser inte ut som ett tal"
                )


def test_values_are_positionally_aligned_with_columns(document_rows):
    """Varje rad måste ha exakt lika många värdeplatser som kolumner, och
    varje kolumn måste ha en rubrik.

    Detta skyddar mot den bugg som hittades i Fas 3: värdena lagrades
    tidigare komprimerade utan luckor, så en rad som saknade värden för
    vissa år (vanligt i elvaårsöversikter) fick värdena förskjutna och
    kopplades till fel år vid läsning."""
    pdf_path, _, rows_by_page = document_rows
    for page_num, rows in rows_by_page.items():
        for row in rows:
            assert row.columns, f"{pdf_path.name} sida {page_num}: saknar kolumnrubriker {row!r}"
            assert len(row.values) == len(row.columns), (
                f"{pdf_path.name} sida {page_num}: {len(row.values)} värdeplatser men "
                f"{len(row.columns)} kolumner i {row.label!r} - värden och kolumner "
                f"är inte positionellt kopplade"
            )
            assert all(c.strip() for c in row.columns), (
                f"{pdf_path.name} sida {page_num}: tom kolumnrubrik i {row!r}"
            )
