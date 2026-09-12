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


def test_fact_chunks_cite_exactly_one_page(document_chunks):
    """En fakta-chunk är EN tabellrad och finns därför på exakt en sida.

    Tidigare ärvde varje fakta-chunk hela räkningens sidlista. För Volvo
    innebar det att en siffra från elvaårsöversikten (s. 224) även
    hänvisades till den segmenterade huvudräkningen (s. 62-63), där den
    tabellen inte finns - källhänvisningen gick alltså inte att slå upp."""
    path, chunks = document_chunks
    for c in chunks:
        if c["chunk_type"] != "fact":
            continue
        assert len(c["pages"]) == 1, (
            f"{path.name}: fakta-chunk {c['chunk_id']} hänvisar till "
            f"{c['pages']} - en enskild tabellrad finns bara på en sida"
        )


def test_fact_chunk_pages_exist_in_source_document(document_chunks):
    """Sidan en fakta-chunk hänvisar till måste vara en sida där den
    räkningen faktiskt lokaliserades."""
    path, chunks = document_chunks
    doc = json.loads((path.parent.parent / "processed" / path.name).read_text(encoding="utf-8"))
    for c in chunks:
        if c["chunk_type"] != "fact":
            continue
        valid_pages = set(doc["statement_pages"].get(c["section"], []))
        assert set(c["pages"]) <= valid_pages, (
            f"{path.name}: {c['chunk_id']} hänvisar till {c['pages']} men "
            f"{c['section']} finns bara på sidorna {sorted(valid_pages)}"
        )


def test_fact_chunk_text_leads_with_verbatim_row_label(document_chunks):
    """Fixar Y3 (docs/evaluation.md): en fakta-chunks text ska inledas med
    radens EXAKTA etikett (t.ex. "Årets resultat." eller "Årets
    totalresultat.") som en egen fras, före den naturligt formulerade
    meningen. Se _fact_chunk_text i src/chunking.py för den fullständiga
    motiveringen - uppmätt effekt på BM25-rangordningen för det lexikalt
    snarlika radparet "Årets resultat"/"Årets totalresultat"."""
    path, chunks = document_chunks
    doc = json.loads((path.parent.parent / "processed" / path.name).read_text(encoding="utf-8"))
    labels_by_section: dict[str, set[str]] = {}
    for page in doc["pages"]:
        for row in page.get("table_rows", []):
            labels_by_section.setdefault(page.get("statement_type", ""), set()).add(row["label"])

    for c in chunks:
        if c["chunk_type"] != "fact":
            continue
        # Etiketten ska stå ORDAGRANT som chunkens allra första fras, följd
        # av ". " innan resten av meningen. OBS: kan INTE hittas genom att
        # partitionera på första ". " - vissa etiketter innehåller själva
        # en punkt (t.ex. "Räntebärande fordringar inkl. kortfristiga
        # placeringar, netto") - så varje kandidatetikett provas istället
        # med startswith.
        candidates = labels_by_section.get(c["section"], set())
        assert any(c["text"].startswith(f"{label}. ") for label in candidates), (
            f"{path.name}: {c['chunk_id']} börjar inte med någon känd radetikett "
            f"följd av '. ': {c['text']!r}"
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
