"""Verifierar chunkningen från Fas 2, steg 3: att ingen chunk saknar
metadata och att inga uppenbara dubbletter uppstår mellan chunkar.

Körs mot samtliga chunkade dokument i data/chunks/ (genererade av
`python -m src.chunking` från data/processed/, se src/chunking.py).
"""

import json
from collections import Counter
from pathlib import Path

import pytest

CHUNKS_DIR = Path(__file__).resolve().parent.parent / "data" / "chunks"
VALID_STATEMENT_TYPES = {"resultaträkning", "balansräkning", "kassaflödesanalys"}


def _all_chunk_files() -> list[Path]:
    files = sorted(CHUNKS_DIR.glob("*.json"))
    assert files, (
        f"inga chunkade dokument hittades i {CHUNKS_DIR} - kör "
        "'python -m src.chunking' innan testerna"
    )
    return files


@pytest.fixture(scope="module", params=_all_chunk_files(), ids=lambda p: p.name)
def document_chunks(request):
    return request.param, json.loads(request.param.read_text(encoding="utf-8"))


def test_no_chunk_missing_metadata(document_chunks):
    path, chunks = document_chunks
    assert chunks, f"{path.name}: inga chunkar alls"
    for c in chunks:
        for field in ("chunk_id", "document", "company", "fiscal_year", "chunk_type", "pages", "text"):
            assert c.get(field), f"{path.name}: chunk saknar '{field}': {c!r}"
        assert c["chunk_type"] in ("text", "table", "fact"), f"{path.name}: okänd chunk_type {c!r}"
        assert isinstance(c["pages"], list) and all(isinstance(p, int) for p in c["pages"]), (
            f"{path.name}: 'pages' ska vara en lista av sidnummer {c!r}"
        )
        if c["chunk_type"] in ("table", "fact"):
            assert c["section"] in VALID_STATEMENT_TYPES, (
                f"{path.name}: {c['chunk_type']}-chunk med ogiltig 'section' {c!r}"
            )
        else:
            assert c["section"] is None, f"{path.name}: textchunk ska ha section=None {c!r}"


def test_chunk_ids_unique_within_document(document_chunks):
    path, chunks = document_chunks
    ids = [c["chunk_id"] for c in chunks]
    duplicates = [cid for cid, count in Counter(ids).items() if count > 1]
    assert not duplicates, f"{path.name}: dubbla chunk_id: {duplicates}"


def test_no_duplicate_chunk_text_within_document(document_chunks):
    """Två LÖPTEXT-chunkar med identiskt innehåll är ett bugg-tecken (t.ex.
    en återkommande sidfot som fångats som egen chunk). Begränsat till
    chunk_type "text": table/fact-chunkar kan legitimt upprepa samma
    etikett/värde (t.ex. "Årets resultat" som både delsumma och slutsumma
    i en resultaträkning, eller en rad som förekommer likadant i både
    huvudräkningen och Volvos elvaårsöversikt) - det är verklig, upprepad
    data i källdokumentet, inte ett chunkningsfel."""
    path, chunks = document_chunks
    texts = [c["text"].strip() for c in chunks if c["chunk_type"] == "text"]
    duplicates = [text for text, count in Counter(texts).items() if count > 1]
    assert not duplicates, (
        f"{path.name}: {len(duplicates)} textinnehåll förekommer i flera chunkar, "
        f"första exemplet: {duplicates[0][:120]!r}"
    )


def test_table_chunks_present_for_every_located_statement(document_chunks):
    """Varje huvudräkning som Fas 1 lokaliserade ska ha blivit en egen
    tabellchunk - annars har något tappats bort mellan faserna."""
    path, chunks = document_chunks
    processed_path = path.parent.parent / "processed" / path.name
    doc = json.loads(processed_path.read_text(encoding="utf-8"))
    table_sections = {c["section"] for c in chunks if c["chunk_type"] == "table"}
    for statement_type, pages in doc["statement_pages"].items():
        if pages:
            assert statement_type in table_sections, (
                f"{path.name}: {statement_type} lokaliserades på sidor {pages} "
                f"men blev ingen tabellchunk"
            )


def test_text_chunks_within_size_bounds(document_chunks):
    """Textchunkar ska hålla sig inom den avsedda storleksramen - annars
    har mening-grupperingen i _chunk_text gått sönder."""
    path, chunks = document_chunks
    for c in chunks:
        if c["chunk_type"] != "text":
            continue
        assert len(c["text"]) <= 3000, (
            f"{path.name}: textchunk {c['chunk_id']} är {len(c['text'])} tecken - "
            f"orimligt långt för en enda chunk"
        )
