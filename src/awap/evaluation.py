"""Prompt evaluation helpers."""

from __future__ import annotations

from awap.domain import EvaluationCase, EvaluationCaseResult


def score_evaluation_case(
    index: int,
    case: EvaluationCase,
    output: str,
) -> EvaluationCaseResult:
    score = 1.0
    reasons: list[str] = []
    output_lower = output.lower()

    for required in case.expected_contains:
        if required.lower() not in output_lower:
            score -= 0.5
            reasons.append(f"Missing expected text: {required}")

    for blocked in case.blocked_terms:
        if blocked.lower() in output_lower:
            score -= 0.5
            reasons.append(f"Blocked term present: {blocked}")

    score = max(score, 0.0)
    return EvaluationCaseResult(
        index=index,
        input_payload=case.input_payload,
        output=output,
        passed=score >= 0.5 and not reasons,
        score=score,
        reasons=reasons,
    )
