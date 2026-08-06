"""Indexerar Fas 2:s chunkar i en lokal Chroma-vektordatabas.

Embeddings skapas med `sentence-transformers` (multilingual-e5-base, se
docs/DECISIONS_FAS3.md för modelljämförelsen) och sparas tillsammans med
chunkens metadata (dokument, bolag, år, sida, sektion, text/tabell) så att
varje sökträff kan källhänvisas.

Chroma tillåter bara str/int/float/bool som metadatavärden - inga listor
och inget None. `pages` (en lista i Fas 2:s chunkformat) lagras därför som
en kommaseparerad sträng, och `section` (None för textchunkar) lagras som
tom sträng.
"""

import json
from pathlib import Path

import chromadb
from sentence_transformers import SentenceTransformer

COLLECTION_NAME = "arsredovisningar"
EMBEDDING_MODEL = "intfloat/multilingual-e5-base"
# E5-modellerna är tränade med asymmetriska prefix och tappar mätbart i
# kvalitet utan dem: dokument ska embeddas som "passage: ..." och frågor som
# "query: ...". Prefixen hör därför ihop med modellvalet och definieras här,
# så att indexering (denna modul) och sökning (src/search.py) inte kan
# råka använda olika konventioner.
QUERY_PREFIX = "query: "
PASSAGE_PREFIX = "passage: "
_BATCH_SIZE = 64


def embed_passages(model: SentenceTransformer, texts: list[str]) -> list[list[float]]:
    return model.encode(
        [PASSAGE_PREFIX + t for t in texts],
        batch_size=_BATCH_SIZE,
        normalize_embeddings=True,
        show_progress_bar=False,
    ).tolist()


def embed_query(model: SentenceTransformer, query: str) -> list[float]:
    return model.encode(
        [QUERY_PREFIX + query], normalize_embeddings=True, show_progress_bar=False
    ).tolist()[0]


def _chunk_to_metadata(chunk: dict) -> dict:
    return {
        "document": chunk["document"],
        "company": chunk["company"],
        "fiscal_year": chunk["fiscal_year"],
        "chunk_type": chunk["chunk_type"],
        "section": chunk["section"] or "",
        "pages": ",".join(str(p) for p in chunk["pages"]),
    }


def build_index(chunks_dir: Path, persist_dir: Path, collection_name: str = COLLECTION_NAME):
    client = chromadb.PersistentClient(path=str(persist_dir))
    try:
        client.delete_collection(collection_name)
    except Exception:
        pass
    # Chroma defaulter till L2-avstånd (euklidiskt) om inget anges - fel mått
    # för sentence-transformers embeddings, som är avsedda att jämföras med
    # cosinuslikhet. Se docs/DECISIONS_FAS3.md.
    collection = client.create_collection(collection_name, metadata={"hnsw:space": "cosine"})

    model = SentenceTransformer(EMBEDDING_MODEL)

    total = 0
    for chunk_path in sorted(chunks_dir.glob("*.json")):
        chunks = json.loads(chunk_path.read_text(encoding="utf-8"))
        if not chunks:
            continue
        for start in range(0, len(chunks), _BATCH_SIZE):
            batch = chunks[start:start + _BATCH_SIZE]
            ids = [c["chunk_id"] for c in batch]
            texts = [c["text"] for c in batch]
            metadatas = [_chunk_to_metadata(c) for c in batch]
            embeddings = embed_passages(model, texts)
            collection.add(ids=ids, embeddings=embeddings, documents=texts, metadatas=metadatas)
            total += len(batch)

    return collection, total


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    collection, total = build_index(root / "data" / "chunks", root / "data" / "chroma")
    print(f"Indexerade {total} chunkar i collection '{collection.name}' ({collection.count()} totalt i DB)")
