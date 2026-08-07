"""Engångsskript: bygger om det lokala Chroma-indexet mot Chroma Cloud.

Kräver att `CHROMA_API_KEY` (och normalt `CHROMA_TENANT`/`CHROMA_DATABASE`)
finns i `.env` - se `.env.example`. Återanvänder embedding-logiken och
chunk-till-metadata-mappningen från `src/vectorstore.py` rakt av, så att
molnindexet blir en kopia av det lokala och inte en parallell
implementation som kan divergera.

**Undantag - stora tabellchunkar:** Chroma Cloud har en kvot på 16 384
bytes per dokument (gratis/startnivå). Tre av 6855 chunkar - Volvos hela
balansräkning per år (medvetet ej delade mitt i tabellen, se
docs/DECISIONS_FAS2.md) - är större än så. De delas HÄR, bara för
molnuppladdningen, i mindre delar (`::del1`, `::del2`, ...) så att
inbäddningen får plats. Den lokala `data/chunks/`-datan och det lokala
Chroma-indexet rörs INTE - `src/hybrid_search.py` slår vid behov tillbaka
på bas-id:t för att hämta hela originaltabellen till svaret, så att
källhänvisningen och den fullständiga tabellkontexten är oförändrad.
"""

import json
from pathlib import Path

import chromadb
from dotenv import load_dotenv

from .vectorstore import _BATCH_SIZE, _chunk_to_metadata, embed_passages

load_dotenv()  # måste köras innan chromadb.CloudClient() läser os.environ

_MAX_DOC_BYTES = 15000  # marginal under Chroma Clouds 16384-byte-kvot


def _split_oversized(chunk: dict) -> list[dict]:
    """Delar en chunk vid meningsgränser (". ") om den överskrider
    molnkvoten. Varje meningsgräns motsvarar en hel tabellrad (se
    `_row_sentence()` i src/chunking.py), så en delning skär aldrig av en
    enskild post mitt i."""
    text = chunk["text"]
    if len(text.encode("utf-8")) <= _MAX_DOC_BYTES:
        return [chunk]

    sentences = text.split(". ")
    parts: list[str] = []
    current = ""
    for i, sentence in enumerate(sentences):
        piece = sentence if i == len(sentences) - 1 else sentence + ". "
        if current and len((current + piece).encode("utf-8")) > _MAX_DOC_BYTES:
            parts.append(current)
            current = piece
        else:
            current += piece
    if current:
        parts.append(current)

    return [
        {**chunk, "chunk_id": f"{chunk['chunk_id']}::del{i + 1}", "text": part}
        for i, part in enumerate(parts)
    ]


def migrate(chunks_dir: Path, client: chromadb.ClientAPI, collection_name: str) -> tuple:
    from sentence_transformers import SentenceTransformer

    from .vectorstore import EMBEDDING_MODEL

    try:
        client.delete_collection(collection_name)
    except Exception:
        pass
    collection = client.create_collection(collection_name, metadata={"hnsw:space": "cosine"})

    model = SentenceTransformer(EMBEDDING_MODEL)

    total = 0
    split_report: list[str] = []
    for chunk_path in sorted(chunks_dir.glob("*.json")):
        chunks = json.loads(chunk_path.read_text(encoding="utf-8"))
        if not chunks:
            continue
        expanded: list[dict] = []
        for c in chunks:
            pieces = _split_oversized(c)
            if len(pieces) > 1:
                split_report.append(f"{c['chunk_id']} -> {len(pieces)} delar")
            expanded.extend(pieces)

        for start in range(0, len(expanded), _BATCH_SIZE):
            batch = expanded[start:start + _BATCH_SIZE]
            ids = [c["chunk_id"] for c in batch]
            texts = [c["text"] for c in batch]
            metadatas = [_chunk_to_metadata(c) for c in batch]
            embeddings = embed_passages(model, texts)
            collection.add(ids=ids, embeddings=embeddings, documents=texts, metadatas=metadatas)
            total += len(batch)

    if split_report:
        print("Delade chunkar (endast i molnet, inte lokalt):")
        for line in split_report:
            print(f"  {line}")

    return collection, total


if __name__ == "__main__":
    import os

    from .vectorstore import COLLECTION_NAME

    root = Path(__file__).resolve().parent.parent
    client = chromadb.CloudClient(
        api_key=os.environ["CHROMA_API_KEY"],
        tenant=os.environ.get("CHROMA_TENANT"),
        database=os.environ.get("CHROMA_DATABASE"),
    )
    collection, total = migrate(root / "data" / "chunks", client, COLLECTION_NAME)
    print(f"Migrerade {total} chunkar till Chroma Cloud, collection '{collection.name}' ({collection.count()} totalt i molnet)")
