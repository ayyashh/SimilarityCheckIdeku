from __future__ import annotations

import math
import re
from typing import List, Optional

from sentence_transformers import SentenceTransformer


MODEL_NAME = "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"


class EmbeddingService:
    """Create and validate a semantic embedding for one text input."""

    def __init__(self, model_name: str = MODEL_NAME) -> None:
        self.model: SentenceTransformer = SentenceTransformer(model_name)
        self._dimension = self._resolve_dimension()

    def _resolve_dimension(self) -> int:
        if hasattr(self.model, "get_embedding_dimension"):
            return self.model.get_embedding_dimension()
        return self.model.get_sentence_embedding_dimension()

    @staticmethod
    def normalize_text(text: Optional[str]) -> str:
        if text is None:
            return ""
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        return re.sub(r"\s+", " ", text).strip()

    def encode(self, text: str) -> List[float]:
        normalized_text = self.normalize_text(text)
        if not normalized_text:
            raise ValueError("Text tidak boleh kosong untuk proses embedding.")

        raw_embedding = self.model.encode(
            normalized_text,
            normalize_embeddings=True,
        )
        embedding = raw_embedding.tolist()
        self._validate_embedding(embedding)
        return embedding

    def _validate_embedding(self, embedding: List[float]) -> None:
        if not embedding:
            raise ValueError("Embedding tidak valid: hasil embedding kosong.")

        if len(embedding) != self._dimension:
            raise ValueError(
                f"Embedding tidak valid: dimension {len(embedding)} "
                f"tidak sesuai dengan model ({self._dimension})."
            )

        for value in embedding:
            if not math.isfinite(value):
                raise ValueError(
                    "Embedding tidak valid: mengandung NaN atau Infinity."
                )
