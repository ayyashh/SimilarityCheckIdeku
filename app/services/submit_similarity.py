"""
SubmitSimilarityService
==========================

Orchestration untuk fitur Pre-Submission Semantic Similarity Check.

Dipakai saat employee memasukkan Idea Name pada halaman Submit New Idea,
sebelum Idea tersebut disimpan ke database.

Flow:
    Idea Name Draft
        ↓
    Temporary Embedding
        ↓
    Cosine Gate (>= 40%) across all indexed candidates
        ↓
    Gemini Evaluation per Candidate
        ↓
    Cosine + Deterministic Gemini Adjustment
        ↓
    Metadata Enrichment
        ↓
        ↓
    SubmitSimilarityResult

Tidak melakukan:
- penyimpanan embedding
- indexing
- attachment extraction
- OCR
- database insert
"""

from __future__ import annotations

import logging
import re

from dataclasses import dataclass, field
from typing import List, Optional, Set

from app.services.embedding import EmbeddingService
from app.repositories.ideas import IdeaRepository
from app.repositories.similarity_index import SimilarityRepository
from app.services.similarity import SimilarityResult, SimilarityService
from app.services.gemini_verifier import GeminiEvaluationError, GeminiVerifier
from app.services.groq_verifier import GroqVerifier
from app.core.config import (
    GROQ_MODEL_120B,
    GROQ_MODEL_QWEN_27B,
    SIMILARITY_AI_MAX_CANDIDATES,
    SIMILARITY_SUBMIT_THRESHOLD,
    SIMILARITY_TOP_K,
)


logger = logging.getLogger(__name__)


# ======================================================================
# EXCEPTION
# ======================================================================

class InvalidIdeaNameError(ValueError):
    """ideaName kosong atau hanya berisi whitespace."""


# ======================================================================
# INTERNAL RESPONSE MODELS
# ======================================================================

@dataclass
class SubmitSimilarityEvidence:

    highlight: str
    field: str
    query_text: str
    candidate_text: str


@dataclass
class SubmitSimilarityReason:

    summary: str

    matched_fields: List[str] = field(
        default_factory=list
    )

    highlights: List[str] = field(
        default_factory=list
    )

    evidence: List[SubmitSimilarityEvidence] = field(
        default_factory=list
    )


@dataclass
class SubmitSimilarityDifference:

    summary: str

    query_focus: List[str] = field(
        default_factory=list
    )

    candidate_focus: List[str] = field(
        default_factory=list
    )

    differences: List[str] = field(
        default_factory=list
    )

    shared_points: List[str] = field(
        default_factory=list
    )


@dataclass
class SubmitSimilarityCandidate:

    idea_id: int

    idea_name: str

    description: str

    idea_solution: str

    to_division: str

    similarity_percentage: float

    reason: SubmitSimilarityReason

    difference: SubmitSimilarityDifference

    cosine_similarity_percentage: float

    ai_verdict: Optional[str] = None

    is_opposite: Optional[bool] = None


@dataclass
class SubmitSimilarityResult:

    found: bool

    threshold: float

    top_k: int

    results: List[SubmitSimilarityCandidate] = field(
        default_factory=list
    )

    evaluation_method: str = "not_evaluated"

    message: Optional[str] = None

    returned_count: int = 0


# ======================================================================
# SERVICE
# ======================================================================

class SubmitSimilarityService:

    """
    Pre-submission semantic similarity service.

    Query hanya berupa Idea Name draft.

    Candidate berasal dari semantic index yang sudah ada dan kemudian
    diperkaya dengan metadata Idea dari dbo.Ideas.
    """

    # Stopword sederhana untuk kebutuhan extraction focus.
    #
    # Tujuannya BUKAN melakukan NLP preprocessing untuk embedding.
    # Hanya dipakai untuk membuat reason/difference yang lebih mudah
    # dibaca manusia.
    STOPWORDS: Set[str] = {

        # English
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "for",
        "in",
        "on",
        "with",
        "by",
        "from",
        "into",
        "through",
        "using",
        "use",

        # Indonesian
        "dan",
        "atau",
        "yang",
        "untuk",
        "dari",
        "dengan",
        "dalam",
        "pada",
        "ke",
        "di",
        "sebagai",
        "agar",
        "dapat",
        "bisa",
        "akan",
        "adalah",
        "ini",
        "itu",

    }

    # Defaults can be overridden through environment configuration.
    DEFAULT_COSINE_GATE: float = SIMILARITY_SUBMIT_THRESHOLD
    DEFAULT_TOP_K: int = SIMILARITY_TOP_K
    MAX_AI_CANDIDATES: int = SIMILARITY_AI_MAX_CANDIDATES


    def __init__(

        self,

        embedding_service: Optional[EmbeddingService] = None,

        idea_repository: Optional[IdeaRepository] = None,

        similarity_repository: Optional[SimilarityRepository] = None,

        similarity_service: Optional[
            SimilarityService
        ] = None,

    ):

        self.embedding_service = (
            embedding_service
            or EmbeddingService()
        )

        self.idea_repository = (
            idea_repository
            or IdeaRepository()
        )

        self.similarity_repository = (
            similarity_repository
            or SimilarityRepository()
        )

        # Reuse similarity engine yang sama dengan fitur lain.
        self.similarity_service = (
            similarity_service
            or SimilarityService(

                repository=self.similarity_repository,

            )
        )

        self._gemini = GeminiVerifier()
        self._groq = GroqVerifier()
        self._groq_120b = GroqVerifier(model_name=GROQ_MODEL_120B)
        self._groq_qwen_27b = GroqVerifier(model_name=GROQ_MODEL_QWEN_27B)


    # ==================================================================
    # MAIN ENTRY POINT
    # ==================================================================

    def check_similarity(

        self,

        idea_name: str,

        provider: str = "gemini",

    ) -> SubmitSimilarityResult:


        # ==============================================================
        # VALIDATION
        # ==============================================================

        cleaned_idea_name = (
            idea_name or ""
        ).strip()


        if not cleaned_idea_name:

            raise InvalidIdeaNameError(
                "ideaName tidak boleh kosong."
            )


        # ==============================================================
        # ==============================================================
        # TEMPORARY QUERY EMBEDDING
        # ==============================================================

        query_embedding = (
            self.embedding_service.encode(
                cleaned_idea_name
            )
        )


        # ==============================================================
        # BUILD CANDIDATE METADATA
        # ==============================================================

        all_ideas = self.idea_repository.get_all()

        ideas_by_id = {
            int(idea["IdeaId"]): idea
            for idea in all_ideas
            if idea.get("IdeaId")
        }

        candidate_texts = {
            idea_id: str(idea.get("IdeaName") or "").strip()
            for idea_id, idea in ideas_by_id.items()
            if idea.get("IdeaName")
        }

        # ==============================================================
        # HYBRID CANDIDATE RETRIEVAL
        # ==============================================================

        cosine_results = self.similarity_service.score_all_candidates(
            query_embedding=query_embedding,
            # Draft belum berada di database.
            query_idea_id=None,
        )
        cosine_by_id = {
            result.idea_id: result.similarity_score
            for result in cosine_results
        }
        candidate_ids = self._build_candidate_pool(
            cosine_results=cosine_results,
            candidate_texts=candidate_texts,
            threshold=self.DEFAULT_COSINE_GATE,
        )

        if not candidate_ids:
            return SubmitSimilarityResult(
                found=False,
                threshold=self.DEFAULT_COSINE_GATE,
                top_k=self.DEFAULT_TOP_K,
                results=[],
                evaluation_method="not_evaluated",
                message="No sufficiently similar ideas found.",
                returned_count=0,
            )

        retrieval_candidates = [
            {
                "idea_id": idea_id,
                "idea_name": candidate_texts[idea_id],
                "cosine_score": cosine_by_id[idea_id],
                "candidate_summary": self._candidate_summary(
                    ideas_by_id[idea_id]
                ),
            }
            for idea_id in candidate_ids
        ]

        if provider not in {"gemini", "groq", "groq_120b", "groq_qwen_27b"}:
            raise ValueError(f"LLM provider tidak didukung: {provider}")
        evaluators = {
            "gemini": self._gemini,
            "groq": self._groq,
            "groq_120b": self._groq_120b,
            "groq_qwen_27b": self._groq_qwen_27b,
        }
        evaluator = evaluators[provider]
        provider_name = provider.upper()
        evaluation_method = provider
        evaluation_message: Optional[str] = None

        try:
            ai_results = evaluator.evaluate_candidates(
                query=cleaned_idea_name,
                candidates=retrieval_candidates,
            )
            if ai_results is not None and len(ai_results) != len(retrieval_candidates):
                raise GeminiEvaluationError(
                    f"Jumlah hasil evaluasi {provider_name} tidak sesuai kandidat."
                )
        except GeminiEvaluationError as ex:
            logger.warning("[SUBMIT SIMILARITY][%s] %s", provider, ex)
            ai_results = None
            evaluation_message = (
                "AI evaluation failed; returned cosine-only scores. "
                "Details: " + str(ex)
            )

        if ai_results is None:
            evaluation_method = "cosine_fallback"
            if evaluation_message is None:
                evaluation_message = (
                    f"{provider_name} is unavailable; returned cosine-only scores."
                )

            evaluations_by_id = {
                candidate["idea_id"]: {
                    "score": candidate["cosine_score"],
                    "adjustment": 0.0,
                    "verdict": None,
                    "reason": None,
                    "is_opposite": None,
                    "shared_points": [],
                    "differences": [],
                }
                for candidate in retrieval_candidates
            }
        else:
            evaluations_by_id = {
                candidate["idea_id"]: evaluation
                for candidate, evaluation in zip(
                    retrieval_candidates,
                    ai_results,
                )
            }

        # Eligibility is based only on initial cosine. Penalties may lower the
        # final score below the gate without removing the qualifying candidate.
        final_results = list(evaluations_by_id.items())
        final_results.sort(
            key=lambda item: (
                item[1]["score"],
                cosine_by_id[item[0]],
                -item[0],
            ),
            reverse=True,
        )
        similarity_results = [
            SimilarityResult(idea_id, evaluation["score"])
            for idea_id, evaluation in final_results
        ]
        final_evaluations_by_id = dict(final_results)

        candidates = self._enrich_candidates(
            similarity_results=similarity_results,
            query_idea_name=cleaned_idea_name,
            ideas_by_id=ideas_by_id,
            cosine_by_id=cosine_by_id,
            evaluations_by_id=final_evaluations_by_id,
        )

        if not candidates:
            logger.warning(
                "[SUBMIT SIMILARITY] Similarity candidates ditemukan "
                "tetapi metadata Idea tidak tersedia."
            )

        return SubmitSimilarityResult(
            found=bool(candidates),
            threshold=self.DEFAULT_COSINE_GATE,
            top_k=self.DEFAULT_TOP_K,
            results=candidates,
            evaluation_method=evaluation_method,
            message=(
                evaluation_message
                if evaluation_message
                else (
                    None
                    if candidates
                    else "No sufficiently similar ideas found."
                )
            ),
            returned_count=len(candidates),
        )

    @staticmethod
    def _candidate_summary(idea: dict) -> str:
        summary = " ".join(
            part
            for part in (
                str(idea.get("Background") or "").strip(),
                str(idea.get("Solution") or "").strip(),
            )
            if part
        )
        return summary[:GeminiVerifier.MAX_CANDIDATE_SUMMARY_CHARS]

    def _build_candidate_pool(
        self,
        cosine_results: List[SimilarityResult],
        candidate_texts: dict[int, str],
        threshold: float = DEFAULT_COSINE_GATE,
    ) -> List[int]:
        """Return valid indexed ideas at or above the configured cosine threshold."""
        best_scores: dict[int, float] = {}
        for result in cosine_results:
            if (
                result.idea_id not in candidate_texts
                or not candidate_texts[result.idea_id]
                or result.similarity_score < threshold
            ):
                continue
            best_scores[result.idea_id] = max(
                result.similarity_score,
                best_scores.get(result.idea_id, float("-inf")),
            )

        ordered_ids = sorted(
            best_scores,
            key=lambda idea_id: (-best_scores[idea_id], idea_id),
        )
        if self.MAX_AI_CANDIDATES:
            return ordered_ids[:self.MAX_AI_CANDIDATES]
        return ordered_ids


    # ==================================================================
    # ENRICH CANDIDATES
    # ==================================================================

    def _enrich_candidates(

        self,

        similarity_results: List[SimilarityResult],

        query_idea_name: str,

        ideas_by_id: dict[int, dict],

        cosine_by_id: dict[int, float],

        evaluations_by_id: dict[int, dict],

    ) -> List[SubmitSimilarityCandidate]:


        candidates: List[
            SubmitSimilarityCandidate
        ] = []


        for result in similarity_results:


            # ==========================================================
            # GET IDEA METADATA
            # ==============================================================

            idea = ideas_by_id.get(result.idea_id)


            if idea is None:

                logger.warning(

                    "[SUBMIT SIMILARITY] Metadata tidak ditemukan "
                    "untuk IdeaId=%s",

                    result.idea_id,

                )

                continue


            # ==========================================================
            # SAFE FIELD EXTRACTION
            # ==============================================================

            candidate_idea_name = str(
                idea.get("IdeaName") or ""
            ).strip()


            candidate_background = str(
                idea.get("Background") or ""
            ).strip()


            candidate_solution = str(
                idea.get("Solution") or ""
            ).strip()


            candidate_division = str(
                idea.get("ToDivision") or ""
            ).strip()


            # ==========================================================
            # GENERATE HUMAN-READABLE EXPLANATION
            # ==============================================================

            reason = self._build_reason(

                query_idea_name=query_idea_name,

                candidate_idea_name=(
                    candidate_idea_name
                ),

                candidate_background=(
                    candidate_background
                ),

                candidate_solution=(
                    candidate_solution
                ),

            )


            # ==========================================================
            # GENERATE DIFFERENCE
            # ==============================================================

            difference = self._build_difference(

                query_idea_name=query_idea_name,

                candidate_idea_name=(
                    candidate_idea_name
                ),

                candidate_background=(
                    candidate_background
                ),

                candidate_solution=(
                    candidate_solution
                ),

            )


            # ==========================================================
            # BUILD CANDIDATE
            # ==========================================================

            evaluation = evaluations_by_id[result.idea_id]
            if evaluation.get("reason"):
                reason.summary = evaluation["reason"]
                difference.differences = evaluation["differences"]
                difference.shared_points = evaluation["shared_points"]

            candidates.append(

                SubmitSimilarityCandidate(

                    idea_id=int(
                        idea.get("IdeaId")
                    ),

                    idea_name=candidate_idea_name,

                    description=candidate_background,

                    idea_solution=candidate_solution,

                    to_division=candidate_division,

                    similarity_percentage=round(
                        result.similarity_score * 100,
                        2,
                    ),

                    reason=reason,

                    difference=difference,

                    cosine_similarity_percentage=round(
                        cosine_by_id[result.idea_id] * 100,
                        2,
                    ),

                    ai_verdict=evaluation.get("verdict"),

                    is_opposite=evaluation.get("is_opposite"),

                )

            )


        return candidates


    # ==================================================================
    # REASON GENERATION
    # ==================================================================

    def _build_reason(

        self,

        query_idea_name: str,

        candidate_idea_name: str,

        candidate_background: str,

        candidate_solution: str,

    ) -> SubmitSimilarityReason:


        # ==============================================================
        # QUERY TOKENS
        # ==============================================================

        query_terms = self._extract_focus_terms(
            query_idea_name
        )


        # ==============================================================
        # FIELD MATCHES
        # ==============================================================

        field_matches = []


        idea_name_matches = (
            self._find_common_terms(

                query_terms,

                self._extract_focus_terms(
                    candidate_idea_name
                ),

            )
        )


        background_matches = (
            self._find_common_terms(

                query_terms,

                self._extract_focus_terms(
                    candidate_background
                ),

            )
        )


        solution_matches = (
            self._find_common_terms(

                query_terms,

                self._extract_focus_terms(
                    candidate_solution
                ),

            )
        )


        if idea_name_matches:

            field_matches.append(
                "IdeaName"
            )


        if background_matches:

            field_matches.append(
                "Background"
            )


        if solution_matches:

            field_matches.append(
                "Solution"
            )


        # ==============================================================
        # HIGHLIGHTS
        # ==============================================================

        all_matches = []


        for item in (
            idea_name_matches
            + background_matches
            + solution_matches
        ):

            if item not in all_matches:

                all_matches.append(item)


        highlights = all_matches[:5]


        # ==============================================================
        # EVIDENCE
        # ==============================================================

        evidence: List[
            SubmitSimilarityEvidence
        ] = []


        for highlight in highlights:


            if highlight in idea_name_matches:

                evidence.append(

                    SubmitSimilarityEvidence(

                        highlight=highlight,

                        field="IdeaName",

                        query_text=query_idea_name,

                        candidate_text=(
                            candidate_idea_name
                        ),

                    )

                )


            elif highlight in background_matches:

                evidence.append(

                    SubmitSimilarityEvidence(

                        highlight=highlight,

                        field="Background",

                        query_text=query_idea_name,

                        candidate_text=(
                            candidate_background
                        ),

                    )

                )


            elif highlight in solution_matches:

                evidence.append(

                    SubmitSimilarityEvidence(

                        highlight=highlight,

                        field="Solution",

                        query_text=query_idea_name,

                        candidate_text=(
                            candidate_solution
                        ),

                    )

                )


        # ==============================================================
        # SUMMARY
        # ==============================================================

        if field_matches and highlights:

            fields_text = self._join_naturally(
                field_matches
            )

            highlights_text = self._join_naturally(
                highlights[:3]
            )


            summary = (

                f"Kemiripan terdeteksi terutama pada "
                f"{fields_text} dengan kesamaan istilah seperti "
                f"{highlights_text}."

            )


        elif field_matches:

            fields_text = self._join_naturally(
                field_matches
            )


            summary = (

                f"Kemiripan terdeteksi terutama pada "
                f"{fields_text}."

            )


        else:

            summary = (

                "Kemiripan terdeteksi berdasarkan representasi "
                "semantik keseluruhan, meskipun belum ditemukan "
                "cukup banyak istilah yang sama secara langsung."

            )


        return SubmitSimilarityReason(

            summary=summary,

            matched_fields=field_matches,

            highlights=highlights,

            evidence=evidence,

        )


    # ==================================================================
    # DIFFERENCE GENERATION
    # ==================================================================

    def _build_difference(

        self,

        query_idea_name: str,

        candidate_idea_name: str,

        candidate_background: str,

        candidate_solution: str,

    ) -> SubmitSimilarityDifference:


        # ==============================================================
        # QUERY FOCUS
        # ==============================================================

        query_focus = (
            self._extract_focus_terms(
                query_idea_name
            )[:5]
        )


        # ==============================================================
        # CANDIDATE FOCUS
        # ==============================================================

        candidate_full_text = " ".join(

            filter(

                None,

                [

                    candidate_idea_name,

                    candidate_background,

                    candidate_solution,

                ],

            )

        )


        candidate_focus = (
            self._extract_focus_terms(
                candidate_full_text
            )[:5]
        )


        # ==============================================================
        # COMMON CONTEXT
        # ==============================================================

        common_terms = (
            self._find_common_terms(

                query_focus,

                candidate_focus,

            )
        )


        # ==============================================================
        # SUMMARY
        # ==============================================================

        if common_terms:

            common_text = self._join_naturally(
                common_terms[:3]
            )


            summary = (

                f"Kedua Idea memiliki konteks yang sama pada "
                f"{common_text}, tetapi memiliki perbedaan fokus "
                f"utama."

            )


        else:

            summary = (

                "Kedua Idea memiliki kemiripan secara semantik, "
                "tetapi menggunakan fokus dan istilah yang berbeda."

            )


        # ==============================================================
        # DIFFERENCE SENTENCES
        # ==============================================================

        differences = []


        if query_focus:

            query_text = self._join_naturally(
                query_focus[:3]
            )


            differences.append(

                f"Query Idea lebih berfokus pada "
                f"{query_text}."

            )


        if candidate_focus:

            candidate_text = self._join_naturally(
                candidate_focus[:3]
            )


            differences.append(

                f"Candidate Idea lebih berfokus pada "
                f"{candidate_text}."

            )


        return SubmitSimilarityDifference(

            summary=summary,

            query_focus=query_focus,

            candidate_focus=candidate_focus,

            differences=differences,

        )


    # ==================================================================
    # TEXT HELPERS
    # ==================================================================

    def _extract_focus_terms(

        self,

        text: str,

    ) -> List[str]:


        if not text:

            return []


        normalized = text.lower()


        # Ambil kata dan kombinasi kata sederhana.
        tokens = re.findall(

            r"[a-zA-ZÀ-ÿ0-9]+",

            normalized,

        )


        filtered_tokens = []


        for token in tokens:


            if len(token) < 3:

                continue


            if token in self.STOPWORDS:

                continue


            if token not in filtered_tokens:

                filtered_tokens.append(
                    token
                )


        # ==============================================================
        # SINGLE TERMS
        # ==============================================================

        terms = list(
            filtered_tokens
        )


        # ==============================================================
        # BIGRAMS
        # ==============================================================

        for index in range(
            len(filtered_tokens) - 1
        ):

            bigram = (
                f"{filtered_tokens[index]} "
                f"{filtered_tokens[index + 1]}"
            )


            if bigram not in terms:

                terms.append(
                    bigram
                )


        return terms


    def _find_common_terms(

        self,

        first_terms: List[str],

        second_terms: List[str],

    ) -> List[str]:


        second_set = set(
            second_terms
        )


        common = []


        for term in first_terms:


            if term in second_set:

                if term not in common:

                    common.append(
                        term
                    )


        return common


    @staticmethod
    def _join_naturally(

        values: List[str],

    ) -> str:


        values = [

            value

            for value in values

            if value

        ]


        if not values:

            return ""


        if len(values) == 1:

            return f'"{values[0]}"'


        if len(values) == 2:

            return (
                f'"{values[0]}" dan '
                f'"{values[1]}"'
            )


        quoted = [

            f'"{value}"'

            for value in values

        ]


        return (
            ", ".join(quoted[:-1])
            + ", dan "
            + quoted[-1]
        )
