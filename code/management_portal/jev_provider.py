from __future__ import annotations

import json
import time
import math
from collections.abc import Mapping
from typing import Any

from .ai_provider_registry import AIProviderRegistry, shared_ai_provider_registry


JEV_PROVIDER_KIND = "typesafe_system_one"
JEV_PROVIDER_ID = "jev_primary"
JEV_ENDPOINT = "systemone"
MAX_REQUEST_BYTES = 2_000_000
MAX_QUESTIONS = 128


class JevProviderError(RuntimeError):
    """Jev request or response did not satisfy Portal's typed contract."""


def _json_size(value: object) -> int:
    try:
        return len(
            json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Jev input must be JSON serializable") from exc


def _structured_content(value: object, *, field: str) -> object:
    if not isinstance(value, (str, list, dict)):
        raise ValueError(f"{field} must be text, an array, or an object")
    if isinstance(value, str) and not value.strip():
        raise ValueError(f"{field} must not be empty")
    _json_size(value)
    return value


def _probability(value: object, *, field: str) -> float:
    if isinstance(value, bool):
        raise JevProviderError(f"Jev response {field} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise JevProviderError(f"Jev response {field} must be numeric") from exc
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise JevProviderError(f"Jev response {field} must be between 0 and 1")
    return number


def validate_questions(value: object) -> dict[str, dict[str, Any]]:
    if not isinstance(value, Mapping) or not value:
        raise ValueError("Jev questions must be a non-empty object")
    if len(value) > MAX_QUESTIONS:
        raise ValueError(f"Jev supports at most {MAX_QUESTIONS} questions per call")
    questions: dict[str, dict[str, Any]] = {}
    for raw_id, raw_question in value.items():
        question_id = str(raw_id or "").strip()
        if not question_id or len(question_id) > 120:
            raise ValueError("Jev question IDs must contain 1 to 120 characters")
        if question_id in questions:
            raise ValueError(f"duplicate Jev question ID: {question_id}")
        if not isinstance(raw_question, Mapping):
            raise ValueError(f"Jev question {question_id} must be an object")
        question = dict(raw_question)
        unknown = set(question) - {"type", "instructions", "criteria"}
        if unknown:
            raise ValueError(
                f"Jev question {question_id} has unsupported fields: "
                + ", ".join(sorted(str(item) for item in unknown))
            )
        question_type = str(question.get("type") or "").strip().lower()
        if question_type not in {"choice", "score", "noul"}:
            raise ValueError(
                f"Jev question {question_id} type must be choice, score, or noul"
            )
        normalized: dict[str, Any] = {
            "type": question_type,
            "instructions": _structured_content(
                question.get("instructions"),
                field=f"Jev question {question_id}.instructions",
            ),
        }
        criteria = question.get("criteria")
        if question_type == "choice":
            if not isinstance(criteria, Mapping) or not 1 <= len(criteria) <= 255:
                raise ValueError(
                    f"Jev choice question {question_id} requires 1 to 255 criteria"
                )
            clean_criteria: dict[str, object] = {}
            for raw_label, description in criteria.items():
                label = str(raw_label or "").strip()
                if not label or len(label) > 200:
                    raise ValueError(
                        f"Jev choice question {question_id} has an invalid label"
                    )
                clean_criteria[label] = (
                    None
                    if description is None
                    else _structured_content(
                        description,
                        field=f"Jev choice question {question_id}.{label}",
                    )
                )
            normalized["criteria"] = clean_criteria
        elif question_type == "score":
            if not isinstance(criteria, list) or not 2 <= len(criteria) <= 10:
                raise ValueError(
                    f"Jev score question {question_id} requires 2 to 10 ordered levels"
                )
            normalized["criteria"] = [
                _structured_content(
                    item,
                    field=f"Jev score question {question_id}.criteria[{index}]",
                )
                for index, item in enumerate(criteria)
            ]
        elif criteria is not None:
            if not isinstance(criteria, Mapping) or set(criteria) != {"true", "false"}:
                raise ValueError(
                    f"Jev noul question {question_id} criteria must define true and false"
                )
            normalized["criteria"] = {
                key: _structured_content(
                    criteria[key],
                    field=f"Jev noul question {question_id}.criteria.{key}",
                )
                for key in ("true", "false")
            }
        questions[question_id] = normalized
    return questions


class JevProvider:
    def __init__(
        self,
        registry: AIProviderRegistry = shared_ai_provider_registry,
        provider_id: str = JEV_PROVIDER_ID,
    ) -> None:
        self.registry = registry
        self.provider_id = str(provider_id or "").strip()

    def configuration(self, item: dict[str, Any] | None = None) -> dict[str, Any]:
        item = self.registry.get(self.provider_id) if item is None else item
        if item.get("id") != self.provider_id or not AIProviderRegistry.is_enabled(item):
            raise ValueError("Jev provider configuration does not match the enabled route")
        if str(item.get("provider") or "").strip().lower() != "typesafe":
            raise ValueError("Jev provider must use the TypeSafe service")
        if str(item.get("kind") or "").strip() != JEV_PROVIDER_KIND:
            raise ValueError("Jev provider requires the typesafe_system_one protocol")
        return item

    def enabled_models(self) -> tuple[str, ...]:
        item = self.configuration()
        return tuple(AIProviderRegistry.models(item))

    def decide(
        self,
        *,
        state: object,
        questions: object,
        model: str = "",
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
        configuration: dict[str, Any] | None = None,
        is_cancelled=None,
        observe_transport=None,
    ) -> dict[str, Any]:
        if not isinstance(state, (str, list, dict)):
            raise ValueError("Jev state must be text, an array, or an object")
        clean_questions = validate_questions(questions)
        item = self.configuration(configuration)
        enabled_models = AIProviderRegistry.models(item)
        selected_model = str(model or item.get("default_model") or "").strip()
        if not selected_model or selected_model not in enabled_models:
            raise ValueError(f"Jev model is not enabled: {selected_model or '<empty>'}")
        payload = {
            "state": state,
            "model": selected_model,
            "questions": clean_questions,
        }
        if _json_size(payload) > MAX_REQUEST_BYTES:
            raise ValueError("Jev request exceeds the 2 MB Portal limit")
        timeout = timeout_seconds if timeout_seconds is not None else item.get("timeout_s", 90)
        attempts = 1 + max(0, int(item.get("max_retries") or 0) if max_retries is None else int(max_retries))
        for attempt in range(attempts):
            if is_cancelled and is_cancelled():
                raise InterruptedError("execution was cancelled before Jev call")
            try:
                def observe(report):
                    if observe_transport and not (is_cancelled and is_cancelled()):
                        observe_transport({**report, "attempt_number": attempt + 1})

                response = self.registry.request(
                    self.provider_id, JEV_ENDPOINT, payload,
                    timeout_seconds=max(1.0, min(float(timeout), 360.0)), configuration=item,
                    **({"observe_transport": observe} if observe_transport else {}),
                )
                break
            except Exception as exc:
                if attempt + 1 >= attempts or not AIProviderRegistry.retryable_error(exc):
                    raise
                time.sleep(min(2.0, 0.25 * (attempt + 1)))
        return self._validate_response(response, clean_questions)

    @staticmethod
    def _validate_response(
        response: object,
        questions: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        if not isinstance(response, dict):
            raise JevProviderError("Jev returned an invalid response")
        model = str(response.get("model") or "").strip()
        answers = response.get("answers")
        usage = response.get("usage")
        if not model or not isinstance(answers, dict) or set(answers) != set(questions):
            raise JevProviderError("Jev response does not match the requested questions")
        if not isinstance(usage, dict):
            raise JevProviderError("Jev response is missing usage")
        for question_id, question in questions.items():
            answer = answers.get(question_id)
            expected_type = question["type"]
            if not isinstance(answer, dict) or answer.get("type") != expected_type:
                raise JevProviderError(
                    f"Jev answer type mismatch for question {question_id}"
                )
            if expected_type == "noul":
                _probability(answer.get("noul"), field=f"answers.{question_id}.noul")
            elif expected_type == "choice":
                selected = str(answer.get("choice") or "")
                if selected not in question["criteria"]:
                    raise JevProviderError(
                        f"Jev returned an unknown choice for question {question_id}"
                    )
                _probability(
                    answer.get("confidence"),
                    field=f"answers.{question_id}.confidence",
                )
                probabilities = answer.get("probabilities")
                if not isinstance(probabilities, dict) or set(probabilities) != set(question["criteria"]):
                    raise JevProviderError(
                        f"Jev choice probabilities mismatch for question {question_id}"
                    )
                for label, probability in probabilities.items():
                    _probability(
                        probability,
                        field=f"answers.{question_id}.probabilities.{label}",
                    )
            else:
                score = answer.get("score")
                if isinstance(score, bool):
                    raise JevProviderError(
                        f"Jev score is invalid for question {question_id}"
                    )
                try:
                    numeric_score = float(score)
                except (TypeError, ValueError) as exc:
                    raise JevProviderError(
                        f"Jev score is invalid for question {question_id}"
                    ) from exc
                if not math.isfinite(numeric_score) or not 0 <= numeric_score <= len(question["criteria"]) - 1:
                    raise JevProviderError(
                        f"Jev score is out of range for question {question_id}"
                    )
                _probability(
                    answer.get("confidence"),
                    field=f"answers.{question_id}.confidence",
                )
        return response


shared_jev_provider = JevProvider()
