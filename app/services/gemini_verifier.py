"""Gemini-backed semantic evaluator for idea similarity candidates."""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import quote

from app.services.semantic_evaluator import SemanticEvaluator
from app.core.config import GEMINI_API_KEY, GEMINI_MODEL

logger = logging.getLogger(__name__)


class GeminiEvaluationError(RuntimeError):
    """Raised when Gemini evaluation fails or returns unusable output."""


class GeminiVerifier(SemanticEvaluator):
    """Use Gemini for evaluation while sharing the established score mapping."""

    DEFAULT_MODEL = GEMINI_MODEL
    DEFAULT_TIMEOUT_SECONDS = 60.0
    MAX_RETRIES = 2
    RETRY_DELAY_SECONDS = 1.0
    RETRYABLE_HTTP_STATUSES = {429, 500, 502, 503, 504}

    def __init__(
        self,
        model_name: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.model_name = (model_name or self.DEFAULT_MODEL).strip()
        self.api_key = GEMINI_API_KEY if api_key is None else api_key.strip()
        self.timeout = timeout_seconds

    def evaluate_candidates(
        self,
        query: str,
        candidates: Sequence[Dict[str, Any]],
    ) -> Optional[List[Dict[str, Any]]]:
        if not self.api_key:
            raise GeminiEvaluationError("GEMINI_API_KEY belum dikonfigurasi.")
        if not self.model_name:
            raise GeminiEvaluationError("GEMINI_MODEL belum dikonfigurasi.")
        if not candidates:
            return []

        with ThreadPoolExecutor(
            max_workers=min(self.MAX_CONCURRENT_REQUESTS, len(candidates))
        ) as executor:
            return list(
                executor.map(
                    lambda candidate: self._evaluate_one_candidate(
                        query=query,
                        candidate=candidate,
                    ),
                    candidates,
                )
            )

    def _request_with_retry(self, payload: dict) -> dict:
        endpoint = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{quote(self.model_name, safe='')}:generateContent"
        )
        json_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        last_error: Optional[Exception] = None

        for attempt in range(1, self.MAX_RETRIES + 1):
            request = urllib.request.Request(
                endpoint,
                data=json_bytes,
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": self.api_key,
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as ex:
                details = self._error_details(ex)
                if ex.code not in self.RETRYABLE_HTTP_STATUSES:
                    raise GeminiEvaluationError(
                        f"Gemini API mengembalikan HTTP {ex.code}: {details}"
                    ) from ex
                last_error = ex
                logger.warning(
                    "[GEMINI] HTTP %d pada percobaan %d/%d.",
                    ex.code,
                    attempt,
                    self.MAX_RETRIES,
                )
            except (urllib.error.URLError, OSError) as ex:
                last_error = ex
                logger.warning(
                    "[GEMINI] Gangguan koneksi pada percobaan %d/%d: %s",
                    attempt,
                    self.MAX_RETRIES,
                    ex,
                )
            except (UnicodeDecodeError, json.JSONDecodeError) as ex:
                raise GeminiEvaluationError(
                    f"Respons Gemini bukan JSON yang valid: {ex}"
                ) from ex

            if attempt < self.MAX_RETRIES:
                time.sleep(self.RETRY_DELAY_SECONDS * attempt)

        raise GeminiEvaluationError(
            f"Gemini API gagal setelah {self.MAX_RETRIES} percobaan: {last_error}"
        ) from last_error

    @staticmethod
    def _error_details(error: urllib.error.HTTPError) -> str:
        try:
            body = json.loads(error.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return "detail error tidak tersedia"
        if not isinstance(body, dict) or not isinstance(body.get("error"), dict):
            return "detail error tidak tersedia"
        message = body["error"].get("message", "")
        return str(message)[:500] or "detail error tidak tersedia"

    def _evaluate_one_candidate(
        self,
        query: str,
        candidate: Dict[str, Any],
    ) -> Dict[str, Any]:
        candidate_title = self._clean_text(
            candidate["idea_name"],
            self.MAX_CANDIDATE_TITLE_CHARS,
        )
        candidate_summary = self._clean_text(
            candidate.get("candidate_summary", ""),
            self.MAX_CANDIDATE_SUMMARY_CHARS,
        )
        clean_query = self._clean_text(query, self.MAX_QUERY_CHARS)
        prompt = (
            f"<ide_baru>{clean_query}</ide_baru>\n"
            f"<ide_kandidat>{candidate_title}. {candidate_summary}</ide_kandidat>"
        )
        payload = {
            "systemInstruction": {
                "parts": [{"text": self.SYSTEM_PROMPT}],
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0,
                "maxOutputTokens": 1024,
            },
        }

        try:
            response = self._request_with_retry(payload)
            usage = response.get("usageMetadata", {})
            logger.info(
                "[GEMINI USAGE] model=%s prompt_tokens=%s "
                "candidate_tokens=%s total_tokens=%s",
                self.model_name,
                usage.get("promptTokenCount", "n/a"),
                usage.get("candidatesTokenCount", "n/a"),
                usage.get("totalTokenCount", "n/a"),
            )
            content = response["candidates"][0]["content"]["parts"]
            response_text = "".join(part["text"] for part in content if "text" in part)
            parsed = self._validate_evaluation(json.loads(response_text))
            cosine_score = float(candidate["cosine_score"])
            if not 0.0 <= cosine_score <= 1.0:
                raise ValueError("Cosine kandidat harus berada pada rentang [0, 1].")

            score = self.score_mapping(parsed, cosine_score)
            verdict = (
                "CONFLICT"
                if parsed["is_opposite"]
                else self._normalize_verdict(score)
            )
            return {
                "score": score,
                "adjustment": score - cosine_score,
                "verdict": verdict,
                "reason": parsed["reason"],
                "is_opposite": parsed["is_opposite"],
                "spok_relation": parsed["spok_relation"],
                "shared_points": parsed["shared_points"],
                "differences": parsed["differences"],
                "llm_model": self.model_name,
            }
        except GeminiEvaluationError:
            raise
        except (IndexError, KeyError, TypeError, ValueError) as ex:
            raise GeminiEvaluationError(
                f"Gagal memvalidasi respons Gemini: {ex}"
            ) from ex
