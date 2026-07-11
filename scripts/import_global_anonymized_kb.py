from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.db.registry  # noqa: E402,F401
from app.db.session import AsyncSessionLocal  # noqa: E402
from app.features.rag.embedding import (  # noqa: E402
    HashEmbeddingProvider,
    SentenceTransformerEmbeddingProvider,
)
from app.features.rag.ingestion import GLOBAL_BASE_SCOPE, RagIngestionService  # noqa: E402
from app.features.rag.repository import RagRepository  # noqa: E402


async def import_global_kb(
    *,
    corpus_dir: Path,
    use_hash_embeddings: bool,
) -> None:
    files = sorted(corpus_dir.glob("*.md"))
    if not files:
        raise SystemExit(f"No .md files found in {corpus_dir}")

    embedding_provider = (
        HashEmbeddingProvider()
        if use_hash_embeddings
        else SentenceTransformerEmbeddingProvider()
    )

    imported = 0
    duplicates = 0
    chunks = 0

    async with AsyncSessionLocal() as session:
        service = RagIngestionService(
            RagRepository(session),
            embedding_provider,
        )
        for path in files:
            result = await service.ingest_text(
                text=path.read_text(encoding="utf-8"),
                original_filename=path.name,
                source_kind="kb_draft",
                corpus_scope=GLOBAL_BASE_SCOPE,
            )
            if result.duplicate:
                duplicates += 1
            else:
                imported += 1
                chunks += result.chunk_count
            await session.commit()
            print(
                f"{path.name}: "
                f"{'duplicate' if result.duplicate else 'imported'} "
                f"({result.chunk_count} chunks)"
            )

    print(
        f"Done. imported={imported}, duplicates={duplicates}, chunks={chunks}, "
        f"scope={GLOBAL_BASE_SCOPE}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import anonymized anticipatory bail drafts into global RAG KB."
    )
    parser.add_argument(
        "corpus_dir",
        type=Path,
        help="Directory containing anonymized .md drafts.",
    )
    parser.add_argument(
        "--hash-embeddings",
        action="store_true",
        help="Use deterministic test embeddings instead of sentence-transformers.",
    )
    args = parser.parse_args()

    asyncio.run(
        import_global_kb(
            corpus_dir=args.corpus_dir.resolve(),
            use_hash_embeddings=args.hash_embeddings,
        )
    )


if __name__ == "__main__":
    main()
