"""Groq-backed semantic evaluator using the shared evaluator methodology."""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Sequence

from app.core.config import GROQ_API_KEY, GROQ_MODEL
from app.services.gemini_verifier import GeminiEvaluationError
from app.services.semantic_evaluator import SemanticEvaluator

logger = logging.getLogger(__name__)


class GroqEvaluationError(GeminiEvaluationError):
    """Raised when Groq evaluation fails or returns unusable output."""


class GroqVerifier(SemanticEvaluator):
    """Evaluate candidates through Groq's OpenAI-compatible chat API."""

    DEFAULT_MODEL = GROQ_MODEL
    MAX_CONCURRENT_REQUESTS = 1
    DEFAULT_TIMEOUT_SECONDS = 60.0
    MAX_RETRIES = 2
    RETRY_DELAY_SECONDS = 1.0
    RETRYABLE_HTTP_STATUSES = {429, 500, 502, 503, 504}
    ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(
        self,
        model_name: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.model_name = (model_name or self.DEFAULT_MODEL).strip()
        self.api_key = GROQ_API_KEY if api_key is None else api_key.strip()
        self.timeout = timeout_seconds

    def evaluate_candidates(
        self,
        query: str,
        candidates: Sequence[Dict[str, Any]],
    ) -> Optional[List[Dict[str, Any]]]:
        if not self.api_key:
            raise GroqEvaluationError("GROQ_API_KEY belum dikonfigurasi.")
        if not self.model_name:
            raise GroqEvaluationError("GROQ_MODEL belum dikonfigurasi.")
        if not candidates:
            return []

        with ThreadPoolExecutor(
            max_workers=min(self.MAX_CONCURRENT_REQUESTS, len(candidates))
        ) as executor:
            return list(
                executor.map(
                    lambda candidate: self._evaluate_one_candidate(query, candidate),
                    candidates,
                )
            )

    def _request_with_retry(self, payload: dict) -> dict:
        json_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        last_error: Optional[Exception] = None

        for attempt in range(1, self.MAX_RETRIES + 1):
            request = urllib.request.Request(
                self.ENDPOINT,
                data=json_bytes,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                    "User-Agent": "IdeaSimilarityCheck/1.0",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as ex:
                details = self._error_details(ex)
                if ex.code not in self.RETRYABLE_HTTP_STATUSES:
                    raise GroqEvaluationError(
                        f"Groq API mengembalikan HTTP {ex.code}: {details}"
                    ) from ex
                last_error = ex
                logger.warning(
                    "[GROQ] HTTP %d pada percobaan %d/%d: %s",
                    ex.code,
                    attempt,
                    self.MAX_RETRIES,
                    details,
                )
            except (urllib.error.URLError, OSError) as ex:
                last_error = ex
                logger.warning(
                    "[GROQ] Gangguan koneksi pada percobaan %d/%d: %s",
                    attempt,
                    self.MAX_RETRIES,
                    ex,
                )
            except (UnicodeDecodeError, json.JSONDecodeError) as ex:
                raise GroqEvaluationError(
                    f"Respons Groq bukan JSON yang valid: {ex}"
                ) from ex

            if attempt < self.MAX_RETRIES:
                retry_after = self._retry_after_seconds(last_error) if isinstance(
                    last_error, urllib.error.HTTPError
                ) else None
                delay = retry_after or self.RETRY_DELAY_SECONDS * attempt
                time.sleep(min(delay, 30.0))

        raise GroqEvaluationError(
            f"Groq API gagal setelah {self.MAX_RETRIES} percobaan: {last_error}"
        ) from last_error

    @staticmethod
    def _retry_after_seconds(error: urllib.error.HTTPError) -> Optional[float]:
        value = error.headers.get("Retry-After") if error.headers else None
        if not value:
            return None
        try:
            return max(float(value), 0.0)
        except ValueError:
            return None

    @staticmethod
    def _error_details(error: urllib.error.HTTPError) -> str:
        try:
            raw_body = error.read().decode("utf-8", errors="replace").strip()
        except OSError:
            return "detail error tidak tersedia"
        if not raw_body:
            return "respons error kosong"

        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError:
            return raw_body[:500]

        if isinstance(body, dict):
            error_body = body.get("error", body)
            if isinstance(error_body, dict):
                message = error_body.get("message") or error_body.get("detail")
                code = error_body.get("code")
                error_type = error_body.get("type")
                details = "; ".join(
                    str(value)
                    for value in (message, f"code={code}" if code else None,
                                  f"type={error_type}" if error_type else None)
                    if value
                )
                if details:
                    return details[:500]

        return raw_body[:500]

    def _evaluate_one_candidate(
        self,
        query: str,
        candidate: Dict[str, Any],
    ) -> Dict[str, Any]:
        candidate_title = self._clean_text(
            candidate["idea_name"], self.MAX_CANDIDATE_TITLE_CHARS
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
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "max_tokens": 1024,
        }

        try:
            parsed = None
            for attempt in range(2):
                response = self._request_with_retry(payload)
                usage = response.get("usage", {})
                logger.info(
                    "[GROQ USAGE] model=%s prompt_tokens=%s "
                    "completion_tokens=%s total_tokens=%s",
                    self.model_name,
                    usage.get("prompt_tokens", "n/a"),
                    usage.get("completion_tokens", "n/a"),
                    usage.get("total_tokens", "n/a"),
                )
                response_text = response["choices"][0]["message"]["content"]
                try:
                    parsed = self._validate_evaluation(
                        self._parse_json_object(response_text)
                    )
                    break
                except (TypeError, ValueError) as ex:
                    if attempt == 1:
                        raise ex
                    logger.warning(
                        "[GROQ] Respons bukan JSON sesuai evaluator; "
                        "mencoba ulang sekali."
                    )
                    payload["messages"] = [
                        *payload["messages"],
                        {"role": "assistant", "content": str(response_text or "")},
                        {
                            "role": "user",
                            "content": (
                                "Respons sebelumnya tidak valid. Keluarkan hanya "
                                "satu objek JSON valid sesuai seluruh field, enum, "
                                "dan tipe yang diwajibkan instruksi evaluator. "
                                "Jangan tambahkan teks atau markdown di luar JSON."
                            ),
                        },
                    ]
            if parsed is None:
                raise ValueError("Groq tidak menghasilkan evaluasi yang valid.")
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
        except GroqEvaluationError:
            raise
        except (IndexError, KeyError, TypeError, ValueError) as ex:
            raise GroqEvaluationError(
                f"Gagal memvalidasi respons Groq: {ex}"
            ) from ex

    @staticmethod
    def _parse_json_object(response_text: str) -> dict:
        """Parse a JSON object even when the model adds fences or a preamble."""
        if not isinstance(response_text, str) or not response_text.strip():
            raise ValueError("Respons Groq kosong atau bukan teks.")
        text = response_text.strip()
        fenced = re.fullmatch(
            r"```(?:json)?\s*(.*?)\s*```",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if fenced:
            text = fenced.group(1).strip()

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed

        decoder = json.JSONDecoder()
        for index, character in enumerate(text):
            if character != "{":
                continue
            try:
                parsed, _ = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed

        raise ValueError("Respons Groq tidak memuat objek JSON yang valid.")
