"""Enkel sökfunktion: text in, topp-k relevanta chunkar ut med källa synlig.

Bygger på Chroma-indexet från `src/vectorstore.py`. Både modellvalet och
frågeprefixet importeras därifrån - frågan måste embeddas med exakt samma
modell och konvention som dokumenten, annars blir avstånden meningslösa.
"""

from dataclasses import dataclass
from pathlib import Path

import chromadb
from sentence_transformers import SentenceTransformer

from .vectorstore import COLLECTION_NAME, EMBEDDING_MODEL, embed_query


@dataclass
class SearchResult:
    chunk_id: str
    document: str
    company: str
    fiscal_year: str
    section: str
    chunk_type: str
    pages: list[int]
    text: str
    distance: float


class Searcher:
    """Laddar modellen och Chroma-collection en gång, återanvänds för flera sökningar."""

    def __init__(self, persist_dir: Path, collection_name: str = COLLECTION_NAME):
        self._client = chromadb.PersistentClient(path=str(persist_dir))
        self._collection = self._client.get_collection(collection_name)
        self._model = SentenceTransformer(EMBEDDING_MODEL)

    def search(self, query: str, top_k: int = 5, where: dict | None = None) -> list[SearchResult]:
        res = self._collection.query(
            query_embeddings=[embed_query(self._model, query)],
            n_results=top_k,
            where=where,
            include=["metadatas", "documents", "distances"],
        )

        results = []
        for i in range(len(res["ids"][0])):
            meta = res["metadatas"][0][i]
            pages = [int(p) for p in meta["pages"].split(",") if p]
            results.append(
                SearchResult(
                    chunk_id=res["ids"][0][i],
                    document=meta["document"],
                    company=meta["company"],
                    fiscal_year=meta["fiscal_year"],
                    section=meta["section"],
                    chunk_type=meta["chunk_type"],
                    pages=pages,
                    text=res["documents"][0][i],
                    distance=res["distances"][0][i],
                )
            )
        return results


def format_result(r: SearchResult, index: int) -> str:
    section = f" ({r.section})" if r.section else ""
    pages = ", ".join(str(p) for p in r.pages)
    header = f"[{index}] {r.document} s.{pages} - {r.chunk_type}{section}"
    # Hybridsökningen rangordnar via fusion och har inget jämförbart
    # avstånd (NaN) - skriv då inte ut ett meningslöst tal.
    if r.distance == r.distance:  # False endast för NaN
        header += f" - avstånd {r.distance:.3f}"
    preview = r.text if len(r.text) <= 300 else r.text[:300] + "..."
    return f"{header}\n{preview}"


if __name__ == "__main__":
    import sys

    root = Path(__file__).resolve().parent.parent
    searcher = Searcher(root / "data" / "chroma")
    query = " ".join(sys.argv[1:]) or "Vad var nettoomsättningen?"
    for i, r in enumerate(searcher.search(query, top_k=5), 1):
        print(format_result(r, i))
        print()
