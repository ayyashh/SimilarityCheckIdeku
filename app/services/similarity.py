"""
SimilarityService
===================
Business Logic layer untuk semantic similarity search.

Tanggung jawab:
- menerima query embedding (+ opsional query_idea_id)
- mengambil candidate embeddings lewat SimilarityRepository
- menghitung cosine similarity
- memfilter berdasarkan threshold
- mengurutkan (descending) & membatasi hasil ke Top-K
- mengecualikan query Idea itu sendiri dari hasil

Threshold dan Top-K menggunakan konfigurasi dari `.env`, dan dapat
dioverride per pemanggilan melalui parameter `find_similar()`.

SimilarityService TIDAK:
- membuat embedding (bukan model AI)
- membaca database secara langsung (selalu lewat SimilarityRepository)
- membaca attachment / CombinedText / IdeaName / dll
- menjalankan indexing / reindex
- mengetahui format filename attachment
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import List, Optional, Sequence

import numpy as np

from app.repositories.similarity_index import (
    SimilarityRepository,
    EmbeddingRecord,
)
from app.core.config import SIMILARITY_REVIEW_THRESHOLD, SIMILARITY_TOP_K

logger = logging.getLogger(__name__)


# ======================================================================
# CONFIGURATION
# ======================================================================

DEFAULT_TOP_K: int = SIMILARITY_TOP_K
DEFAULT_THRESHOLD: float = SIMILARITY_REVIEW_THRESHOLD

_NORM_EPSILON: float = 1e-10


# ======================================================================
# RESULT OBJECT
# ======================================================================

@dataclass
class SimilarityResult:

    idea_id: int
    similarity_score: float

    def to_dict(self) -> dict:

        return {
            "IdeaId": self.idea_id,
            "SimilarityScore": self.similarity_score,
        }


# ======================================================================
# EXCEPTIONS
# ======================================================================

class InvalidQueryEmbeddingError(ValueError):
    """Dilempar apabila query embedding tidak valid (None/kosong/non-numeric)."""


# ======================================================================
# SIMILARITY SERVICE
# ======================================================================

class SimilarityService:

    def __init__(
        self,
        repository: Optional[SimilarityRepository] = None,
        top_k: int = DEFAULT_TOP_K,
        threshold: float = DEFAULT_THRESHOLD,
    ):

        self.repository = repository or SimilarityRepository()

        self.default_top_k = top_k
        self.default_threshold = threshold

        # Feature Engineering: Subject Conflict Detector

    # ==================================================================
    # MAIN ENTRY POINT
    # ==================================================================

    def score_all_candidates(
        self,
        query_embedding: Sequence[float],
        query_idea_id: Optional[int] = None,
    ) -> List[SimilarityResult]:
        """Return raw cosine scores for every valid active candidate."""

        query_vector = self._validate_query_embedding(query_embedding)
        candidates = self._fetch_candidates(query_idea_id)

        return self._score_candidates(
            query_vector=query_vector,
            candidates=candidates,
            query_idea_id=query_idea_id,
        )

    def find_similar(
        self,
        query_embedding: Sequence[float],
        query_idea_id: Optional[int] = None,
        top_k: Optional[int] = None,
        threshold: Optional[float] = None,
        query_text: Optional[str] = None,
        candidate_texts: Optional[dict] = None,
    ) -> List[SimilarityResult]:
        """
        query_text : (opsional) teks asli query, digunakan untuk
            Feature Engineering SubjectConflictDetector.

        candidate_texts : (opsional) dict {idea_id: text}, digunakan
            bersama query_text untuk pendeteksian konflik subjek.
        """

        effective_top_k = top_k if top_k is not None else self.default_top_k

        effective_threshold = (
            threshold if threshold is not None else self.default_threshold
        )

        query_vector = self._validate_query_embedding(query_embedding)

        candidates = self._fetch_candidates(query_idea_id)

        scored_results = self._score_candidates(
            query_vector=query_vector,
            candidates=candidates,
            query_idea_id=query_idea_id,
            query_text=query_text,
            candidate_texts=candidate_texts,
        )

        filtered_results = [
            result for result in scored_results
            if result.similarity_score >= effective_threshold
        ]

        filtered_results.sort(
            key=lambda result: result.similarity_score,
            reverse=True
        )

        return filtered_results[:effective_top_k]

    # ==================================================================
    # SETTINGS RESOLUTION (BARU)
    # ==================================================================

    # ==================================================================
    # QUERY VALIDATION
    # ==================================================================

    def _validate_query_embedding(self, query_embedding: Sequence[float]) -> np.ndarray:

        if query_embedding is None:

            raise InvalidQueryEmbeddingError("Query embedding tidak valid: bernilai None.")

        if len(query_embedding) == 0:

            raise InvalidQueryEmbeddingError("Query embedding tidak valid: kosong.")

        try:

            vector = np.asarray(query_embedding, dtype=np.float64)

        except (TypeError, ValueError) as ex:

            raise InvalidQueryEmbeddingError(
                f"Query embedding tidak valid: gagal dikonversi ke numeric vector ({ex})."
            )

        if not np.all(np.isfinite(vector)):

            raise InvalidQueryEmbeddingError(
                "Query embedding tidak valid: mengandung NaN atau Infinity."
            )

        if np.linalg.norm(vector) <= _NORM_EPSILON:

            raise InvalidQueryEmbeddingError(
                "Query embedding tidak valid: vector nol (zero vector)."
            )

        return vector

    # ==================================================================
    # CANDIDATE FETCH
    # ==================================================================

    def _fetch_candidates(self, query_idea_id: Optional[int]) -> List[EmbeddingRecord]:

        if query_idea_id is not None:

            return self.repository.get_candidates_excluding(query_idea_id)

        return self.repository.get_all_active_embeddings()

    # ==================================================================
    # SCORING
    # ==================================================================

    def _score_candidates(
        self,
        query_vector: np.ndarray,
        candidates: List[EmbeddingRecord],
        query_idea_id: Optional[int],
        query_text: Optional[str] = None,
        candidate_texts: Optional[dict] = None,
    ) -> List[SimilarityResult]:

        query_dimension = query_vector.shape[0]

        results: List[SimilarityResult] = []

        seen_idea_ids = set()

        for candidate in candidates:

            if query_idea_id is not None and candidate.idea_id == query_idea_id:
                continue

            if candidate.idea_id in seen_idea_ids:

                logger.warning(f"[DUPLICATE CANDIDATE SKIPPED] IdeaId={candidate.idea_id}")

                continue

            candidate_vector = self._validate_candidate_embedding(
                candidate=candidate,
                expected_dimension=query_dimension,
            )

            if candidate_vector is None:
                continue

            # Dense cosine similarity
            dense_score = self._cosine_similarity(query_vector, candidate_vector)

            # Feature Engineering: Subject Conflict Penalty / Match Bonus
            if query_text and candidate_texts and candidate.idea_id in candidate_texts:
                candidate_text = candidate_texts[candidate.idea_id]
                similarity_score = self._conflict_detector.apply(
                    text_a=query_text,
                    text_b=candidate_text,
                    dense_score=dense_score,
                )
            else:
                similarity_score = dense_score

            results.append(
                SimilarityResult(
                    idea_id=candidate.idea_id,
                    similarity_score=similarity_score,
                )
            )

            seen_idea_ids.add(candidate.idea_id)

        return results

    def _validate_candidate_embedding(
        self,
        candidate: EmbeddingRecord,
        expected_dimension: int,
    ) -> Optional[np.ndarray]:

        if candidate.embedding is None or len(candidate.embedding) == 0:

            logger.warning(f"[INVALID CANDIDATE SKIPPED] IdeaId={candidate.idea_id} -> embedding None/kosong")

            return None

        try:

            vector = np.asarray(candidate.embedding, dtype=np.float64)

        except (TypeError, ValueError) as ex:

            logger.warning(f"[INVALID CANDIDATE SKIPPED] IdeaId={candidate.idea_id} -> {ex}")

            return None

        if not np.all(np.isfinite(vector)):

            logger.warning(f"[INVALID CANDIDATE SKIPPED] IdeaId={candidate.idea_id} -> NaN/Infinity")

            return None

        if vector.shape[0] != expected_dimension:

            logger.warning(
                f"[DIMENSION MISMATCH SKIPPED] IdeaId={candidate.idea_id} "
                f"-> candidate={vector.shape[0]}, query={expected_dimension}"
            )

            return None

        if np.linalg.norm(vector) <= _NORM_EPSILON:

            logger.warning(f"[INVALID CANDIDATE SKIPPED] IdeaId={candidate.idea_id} -> vector nol")

            return None

        return vector

    # ==================================================================
    # COSINE SIMILARITY
    # ==================================================================

    def _cosine_similarity(self, vector_a: np.ndarray, vector_b: np.ndarray) -> float:

        dot_product = float(np.dot(vector_a, vector_b))

        norm_a = float(np.linalg.norm(vector_a))
        norm_b = float(np.linalg.norm(vector_b))

        similarity = dot_product / (norm_a * norm_b)

        return max(-1.0, min(1.0, similarity))
