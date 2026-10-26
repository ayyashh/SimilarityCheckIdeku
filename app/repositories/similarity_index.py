from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import List, Optional

from app.repositories.database import build_connection_string, safe_connection

logger = logging.getLogger(__name__)


@dataclass
class EmbeddingRecord:
    idea_id: int
    embedding: List[float]
    embedding_dimension: int
    model_name: str


class CorruptEmbeddingError(Exception):
    """Raised when a database embedding cannot be decoded or validated."""

    def __init__(self, idea_id: int, reason: str) -> None:
        self.idea_id = idea_id
        self.reason = reason
        super().__init__(f"IdeaId={idea_id} -> embedding corrupt: {reason}")


class SimilarityRepository:
    """Read active embeddings from dbo.IdeaSemanticIndex."""

    def __init__(self) -> None:
        self.connection_string = build_connection_string()

    @staticmethod
    def _decode_embedding(
        idea_id: int,
        embedding_raw: Optional[str],
        expected_dimension: Optional[int],
    ) -> List[float]:
        if embedding_raw is None or embedding_raw.strip() == "":
            raise CorruptEmbeddingError(idea_id, "embedding NULL/kosong")

        try:
            decoded = json.loads(embedding_raw)
        except (json.JSONDecodeError, TypeError) as ex:
            raise CorruptEmbeddingError(idea_id, f"gagal decode JSON ({ex})") from ex

        if not isinstance(decoded, list):
            raise CorruptEmbeddingError(idea_id, "hasil decode bukan list")
        if not decoded:
            raise CorruptEmbeddingError(idea_id, "embedding list kosong")

        for value in decoded:
            if not isinstance(value, (int, float)):
                raise CorruptEmbeddingError(
                    idea_id,
                    f"elemen non-numeric ditemukan ({type(value).__name__})",
                )

        if expected_dimension is not None and len(decoded) != expected_dimension:
            raise CorruptEmbeddingError(
                idea_id,
                f"dimension mismatch (data={len(decoded)}, expected={expected_dimension})",
            )

        return [float(value) for value in decoded]

    def _get_active_embeddings(
        self,
        excluded_idea_id: Optional[int] = None,
    ) -> List[EmbeddingRecord]:
        sql = """
        SELECT IdeaId, Embedding, EmbeddingDimension, ModelName
        FROM dbo.IdeaSemanticIndex
        WHERE IsActive = 1
        """

        with safe_connection(self.connection_string) as conn:
            cursor = conn.cursor()
            if excluded_idea_id is None:
                rows = cursor.execute(sql).fetchall()
            else:
                rows = cursor.execute(
                    sql + " AND IdeaId <> ?",
                    excluded_idea_id,
                ).fetchall()

        records: List[EmbeddingRecord] = []
        for row in rows:
            try:
                embedding = self._decode_embedding(
                    idea_id=row.IdeaId,
                    embedding_raw=row.Embedding,
                    expected_dimension=row.EmbeddingDimension,
                )
            except CorruptEmbeddingError as ex:
                logger.error("[CORRUPT EMBEDDING SKIPPED] %s", ex)
                continue

            records.append(
                EmbeddingRecord(
                    idea_id=row.IdeaId,
                    embedding=embedding,
                    embedding_dimension=row.EmbeddingDimension,
                    model_name=row.ModelName or "",
                )
            )

        return records

    def get_all_active_embeddings(self) -> List[EmbeddingRecord]:
        """Return valid embeddings for every active indexed idea."""
        return self._get_active_embeddings()

    def get_candidates_excluding(self, idea_id: int) -> List[EmbeddingRecord]:
        """Return valid active embeddings except for the given idea."""
        return self._get_active_embeddings(excluded_idea_id=idea_id)
