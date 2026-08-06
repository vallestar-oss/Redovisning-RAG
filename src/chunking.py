"""Semantisk chunkning av Fas 1:s strukturerade JSON-output.

Två helt olika chunkningsstrategier, en per innehållstyp:

- **Tabellsidor** (statement_type/table_rows från Fas 1): en huvudräkning
  (resultat-/balans-/kassaflödesräkning) blir EN chunk, oavsett hur många
  sidor den spänner över (Volvo delar t.ex. balansräkningen på två sidor -
  TILLGÅNGAR / EGET KAPITAL OCH SKULDER - dessa hör ihop och får inte delas
  upp). Att klippa mitt i en tabell var en av de tydligaste lärdomarna från
  Fas 1: en halv rad utan sina kolumnrubriker är meningslös vid retrieval.
- **Löptextsidor** (ingen table_rows): text som saknar tillförlitliga
  styckesmarkörer i den råa PDF-extraktionen (se docs/DECISIONS_FAS2.md -
  pdfplumber ger en rad text per visuell rad, inte per stycke, särskilt i
  denna typ av flerkolumnslayout). Chunkas därför genom att gruppera hela
  MENINGAR upp till en målstorlek, och klipper ALDRIG mitt i en mening -
  det är den semantiska enhet vi kan lita på givet vad extraktionen ger oss.

Varje chunk bär med sig dokument/bolag/år, sida(sidor) och sektion, vilket
är vad som gör källhänvisning möjlig i senare faser.
"""

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÅÄÖ0-9])")
_TARGET_CHARS = 1000  # eftersträvad chunkstorlek - stort nog för sammanhang, litet nog för precis retrieval
_MAX_CHARS = 1400  # hård gräns för en chunk
_MIN_CHUNK_CHARS = 30  # kortare "text" är bara en löphuvud/sidfot utan eget innehåll (t.ex. "NOTER\nFORTS.") - skippas


@dataclass
class Chunk:
    chunk_id: str
    document: str
    company: str
    fiscal_year: str
    chunk_type: str  # "text" (löptext), "table" (hel huvudräkning) eller "fact" (en tabellrad, naturligt formulerad - se Fas 3)
    section: str | None  # statement_type ("resultaträkning" osv.) för tabellchunkar, annars None
    pages: list[int] = field(default_factory=list)
    text: str = ""


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END_RE.split(text) if s.strip()]


def _split_oversized_unit(unit: str, max_chars: int) -> list[str]:
    """Vissa sidor (leveransstatistik, ESRS-indextabeller, noter med
    inbäddade siffertabeller) har knappt någon meningsskiljande
    punktuering - hela sidan blir då EN "mening" som vida överstiger
    max_chars. Sådana sidor är i praktiken listor/tabeller, inte löpande
    prosa, så vi delar dem på radbrytningar (bevarar varje rad hel) istället
    för att låta en enda jättechunk stå kvar eller klippa godtyckligt."""
    if len(unit) <= max_chars:
        return [unit]
    lines = [ln for ln in unit.split("\n") if ln.strip()]
    pieces: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in lines:
        if current and current_len + len(line) + 1 > max_chars:
            pieces.append("\n".join(current))
            current, current_len = [], 0
        if len(line) > max_chars:
            # en enskild rad längre än max_chars (extremt ovanligt) - sista
            # utväg: hård avklippning, hellre än att aldrig avsluta chunken
            for start in range(0, len(line), max_chars):
                pieces.append(line[start:start + max_chars])
            continue
        current.append(line)
        current_len += len(line) + 1
    if current:
        pieces.append("\n".join(current))
    return pieces


def _chunk_text(text: str, target_chars: int = _TARGET_CHARS, max_chars: int = _MAX_CHARS) -> list[str]:
    """Grupperar hela meningar upp till en målstorlek. Klipper aldrig mitt
    i en mening under normala förhållanden; för sidor utan meningsskiljande
    punktuering (se _split_oversized_unit) delas istället på radbrytningar."""
    sentences = [
        piece
        for sentence in _split_sentences(text)
        for piece in _split_oversized_unit(sentence, max_chars)
    ]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for sentence in sentences:
        if current and current_len + len(sentence) + 1 > max_chars:
            chunks.append(" ".join(current))
            current, current_len = [], 0
        current.append(sentence)
        current_len += len(sentence) + 1
        if current_len >= target_chars:
            chunks.append(" ".join(current))
            current, current_len = [], 0

    if current:
        chunks.append(" ".join(current))
    return chunks


def _format_values(values: list[str]) -> str:
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} (föregående period: {values[1]})"
    return ", ".join(values)  # flerårsöversikt - fler än två perioder


def _row_sentence(company: str, statement_type: str, fiscal_year: str, row: dict) -> str:
    company_cap = company[:1].upper() + company[1:]
    return (
        f"{company_cap} {statement_type} {fiscal_year}: {row['label']} var "
        f"{_format_values(row['values'])}."
    )


def _render_table_chunk(statement_type: str, company: str, fiscal_year: str, rows: list[dict]) -> str:
    """Renderar HELA tabellen som naturligt formulerade meningar, en per
    rad. Fas 3:s retrieval-tester visade att det tidigare "etikett: v1 / v2"-
    formatet låg långt ifrån en naturligt formulerad fråga i embedding-
    rymden (avstånd 0,335 mot 0,180 för samma sakuppgift naturligt
    formulerad - se docs/DECISIONS_FAS3.md) - embeddingmodellen är tränad på
    löpande språk, inte tätt tabellformat."""
    intro = f"{company[:1].upper()}{company[1:]} {statement_type} {fiscal_year}."
    sentences = [_row_sentence(company, statement_type, fiscal_year, row) for row in rows]
    return " ".join([intro] + sentences)


def chunk_document(doc: dict) -> list[Chunk]:
    chunks: list[Chunk] = []
    pages_by_number = {p["page"]: p for p in doc["pages"]}
    statement_page_numbers: set[int] = set()

    for statement_type, page_numbers in doc["statement_pages"].items():
        if not page_numbers:
            continue
        statement_page_numbers.update(page_numbers)
        rows = []
        for pnum in page_numbers:
            rows.extend(pages_by_number[pnum].get("table_rows", []))
        if not rows:
            continue
        text = _render_table_chunk(statement_type, doc["company"], doc["fiscal_year"], rows)
        chunks.append(
            Chunk(
                chunk_id=f"{doc['document']}::{statement_type}",
                document=doc["document"],
                company=doc["company"],
                fiscal_year=doc["fiscal_year"],
                chunk_type="table",
                section=statement_type,
                pages=sorted(page_numbers),
                text=text,
            )
        )

        # Fakta-chunkar: en per rad, naturligt formulerad, UTÖVER hela
        # tabellchunken ovan. En hel huvudräkning som en enda chunk späder ut
        # embeddingen (medelvärdet av 10-20 poster) och gör att en fråga om
        # EN specifik post (t.ex. "nettoomsättning") inte hittar rätt chunk -
        # se docs/DECISIONS_FAS3.md. Radchunkarna ger precision; helhets-
        # chunken ovan behålls för sammanhang (t.ex. "visa hela balans-
        # räkningen").
        for i, row in enumerate(rows):
            chunks.append(
                Chunk(
                    chunk_id=f"{doc['document']}::{statement_type}::rad{i}",
                    document=doc["document"],
                    company=doc["company"],
                    fiscal_year=doc["fiscal_year"],
                    chunk_type="fact",
                    section=statement_type,
                    pages=sorted(page_numbers),
                    text=_row_sentence(doc["company"], statement_type, doc["fiscal_year"], row),
                )
            )

    seen_text: set[str] = set()
    for page in doc["pages"]:
        if page["page"] in statement_page_numbers:
            continue
        page_text = page["text"].strip()
        if len(page_text) < _MIN_CHUNK_CHARS:
            continue  # bara ett löphuvud/sidfot utan eget innehåll, t.ex. "NOTER\nFORTS."
        pieces = [p for p in _chunk_text(page_text) if len(p.strip()) >= _MIN_CHUNK_CHARS]
        for i, piece in enumerate(pieces):
            # Text som förekommer ORDAGRANT igen längre fram i dokumentet är
            # antingen ett återkommande löphuvud/sidfot (t.ex. "Årsredovisning
            # och hållbarhetsrapport 2023 | Hexatronic") eller upprepad text
            # från en redan känt problematisk sida (se docs/DECISIONS_FAS2.md
            # om styrelseledamotssidan i Volvos rapport) - i båda fallen ger en
            # andra identisk chunk inget nytt värde vid retrieval, bara dubbel
            # vikt åt samma innehåll. Endast den första förekomsten behålls.
            normalized = piece.strip()
            if normalized in seen_text:
                continue
            seen_text.add(normalized)
            chunks.append(
                Chunk(
                    chunk_id=f"{doc['document']}::p{page['page']}::{i}",
                    document=doc["document"],
                    company=doc["company"],
                    fiscal_year=doc["fiscal_year"],
                    chunk_type="text",
                    section=None,
                    pages=[page["page"]],
                    text=piece,
                )
            )

    return chunks


def chunk_all(processed_dir: Path, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for doc_path in sorted(processed_dir.glob("*.json")):
        doc = json.loads(doc_path.read_text(encoding="utf-8"))
        chunks = chunk_document(doc)
        out_path = out_dir / doc_path.name
        out_path.write_text(
            json.dumps([asdict(c) for c in chunks], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        written.append(out_path)
    return written


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    result = chunk_all(root / "data" / "processed", root / "data" / "chunks")
    for p in result:
        print(p)
