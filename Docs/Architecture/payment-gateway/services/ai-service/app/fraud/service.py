"""Fraud scoring service — orchestrates rule engine + optional LLM explanation.

Two-tier architecture
---------------------

Tier 1 — Deterministic rules (always runs)
    ``rules.evaluate()`` is synchronous, pure, sub-millisecond, and has
    zero external dependencies. It produces a risk_score and a list of
    RuleHits. This is the hard correctness layer: no LLM outage or quota
    limit can break it.

Tier 2 — LLM explanation (best-effort, no-key fallback)
    When Azure OpenAI is configured and reachable, the service sends a
    compact prompt asking for a one-sentence explanation of the score in
    plain language. If the LLM is unavailable (not configured, timeout,
    quota exhausted, any error), the service falls back to a deterministic
    template explanation. The ``llm_used`` flag in the response tells
    callers which path was taken.

Transactional contract
----------------------
This service has no database. It is stateless and safe to run concurrently.
The caller (route handler) owns request validation; this service owns scoring.

Latency budget
--------------
    Rule evaluation:   < 1 ms
    LLM explanation:   150–600 ms (gpt-4o-mini, ~100 token prompt/response)
    Fallback template: < 1 ms

The route handler passes a ``timeout`` (default 3 s) to the LLM call and
catches ``asyncio.TimeoutError``; the score is returned regardless.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import structlog

from app.fraud.rules import THRESHOLD_REJECT, THRESHOLD_REVIEW, evaluate
from app.fraud.schemas import (
    FraudDecision,
    FraudScoreRequest,
    FraudScoreResponse,
    RuleHit,
)
from app.llm.client import LLMClient, LLMNotConfiguredError

log = structlog.get_logger(__name__)

# Increment when rule weights, thresholds, or the LLM prompt changes.
MODEL_VERSION: str = "deterministic-v1+llm-explain"

# Maximum seconds to wait for the LLM explanation before falling back.
_LLM_TIMEOUT: float = 3.0

# OpenAI model used for explanation (overrides the chat deployment setting).
# We intentionally use the cheaper/faster model for a one-sentence summary.
_EXPLAIN_MAX_TOKENS: int = 120


class FraudScoringService:
    """Stateless service that scores one transaction request.

    Constructor injection makes the LLM dependency explicit and testable:
    pass ``llm=None`` in tests to skip the LLM path entirely without
    environment-variable hacks.
    """

    def __init__(self, llm: LLMClient | None) -> None:
        self._llm = llm

    async def score(self, req: FraudScoreRequest) -> FraudScoreResponse:
        """Score a single transaction and return a fully populated response.

        Steps:
            1. Run the deterministic rule engine (synchronous, always succeeds).
            2. Map the raw score to a decision using the threshold constants.
            3. Attempt LLM explanation (best-effort; falls back to template).
            4. Build and return the response.
        """
        # ── Step 1: deterministic scoring ────────────────────────────────
        raw_score, hits = evaluate(req)

        log.info(
            "fraud_rules_evaluated",
            transaction_id=str(req.transaction_id),
            raw_score=raw_score,
            rules_fired=len(hits),
            top_rule=hits[0].rule_id if hits else "none",
        )

        # ── Step 2: decision ─────────────────────────────────────────────
        decision = self._score_to_decision(raw_score)

        # ── Step 3: explanation ──────────────────────────────────────────
        explanation, llm_used = await self._explain(req, raw_score, decision, hits)

        # ── Step 4: assemble response ────────────────────────────────────
        reasons = [h.reason for h in hits]

        return FraudScoreResponse(
            transaction_id=req.transaction_id,
            user_id=req.user_id,
            risk_score=raw_score,
            decision=decision,
            reasons=reasons,
            rule_hits=hits,
            explanation=explanation,
            model_version=MODEL_VERSION,
            llm_used=llm_used,
            scored_at=datetime.now(timezone.utc).isoformat(),
        )

    # ── Decision mapping ──────────────────────────────────────────────────

    @staticmethod
    def _score_to_decision(score: int) -> FraudDecision:
        """Map a 0–100 score to a three-tier decision.

        Thresholds are imported from ``rules.py`` — the single source of truth.
        """
        if score >= THRESHOLD_REJECT:
            return FraudDecision.reject
        if score >= THRESHOLD_REVIEW:
            return FraudDecision.review
        return FraudDecision.allow

    # ── LLM explanation ───────────────────────────────────────────────────

    async def _explain(
        self,
        req: FraudScoreRequest,
        score: int,
        decision: FraudDecision,
        hits: list[RuleHit],
    ) -> tuple[str, bool]:
        """Return ``(explanation_text, llm_used_bool)``.

        Tries the LLM path first; falls back to a deterministic template.
        """
        if self._llm is not None and self._llm.is_configured:
            try:
                text = await asyncio.wait_for(
                    self._llm_explanation(req, score, decision, hits),
                    timeout=_LLM_TIMEOUT,
                )
                return text, True
            except asyncio.TimeoutError:
                log.warning(
                    "fraud_llm_explain_timeout",
                    transaction_id=str(req.transaction_id),
                    timeout=_LLM_TIMEOUT,
                )
            except LLMNotConfiguredError:
                pass  # fallthrough to template
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "fraud_llm_explain_error",
                    transaction_id=str(req.transaction_id),
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

        return self._template_explanation(score, decision, hits), False

    async def _llm_explanation(
        self,
        req: FraudScoreRequest,
        score: int,
        decision: FraudDecision,
        hits: list[RuleHit],
    ) -> str:
        """Call the LLM and extract the explanation text.

        Prompt is intentionally compact (<150 tokens) to stay fast and cheap.
        We ask for exactly one sentence: long explanations belong in the
        audit UI, not in a payment-flow API response.
        """
        if self._llm is None or not self._llm.is_configured:
            raise LLMNotConfiguredError("LLM not configured")

        # Build a brief rule summary — at most 3 top rules to avoid prompt bloat.
        top_rules = hits[:3]
        rule_summary = (
            "; ".join(
                f"{h.rule_id} (weight {h.weight}): {h.reason[:80]}"
                for h in top_rules
            )
            if top_rules
            else "no rules fired"
        )

        prompt = (
            f"Summarise this payment fraud assessment in ONE sentence for a risk analyst:\n"
            f"Transaction: {req.currency} {req.amount} via {req.payment_method.value} "
            f"to merchant {req.merchant_id}.\n"
            f"Risk score: {score}/100. Decision: {decision.value.upper()}.\n"
            f"Key signals: {rule_summary}.\n"
            f"Be factual and concise. Do not add caveats or recommendations."
        )

        # Access the underlying Azure OpenAI client via the private attribute.
        # This is the documented pattern until LLMClient grows a public chat() method.
        client = self._llm._client  # noqa: SLF001
        settings = self._llm.settings

        response = await client.chat.completions.create(
            model=settings.azure_openai_chat_deployment,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a concise fraud risk summariser. "
                        "Always respond with exactly one sentence."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=_EXPLAIN_MAX_TOKENS,
            temperature=0.2,   # low temperature → consistent, factual output
        )

        text = response.choices[0].message.content or ""
        return text.strip()

    @staticmethod
    def _template_explanation(
        score: int,
        decision: FraudDecision,
        hits: list[RuleHit],
    ) -> str:
        """Deterministic one-sentence explanation when LLM is unavailable."""
        if not hits:
            return (
                f"No fraud signals were detected; the transaction scored {score}/100 "
                f"and is cleared for {decision.value}."
            )

        top = hits[0]
        n = len(hits)
        others = f" and {n - 1} other signal{'s' if n > 1 else ''}" if n > 1 else ""

        decision_text = {
            FraudDecision.allow: "cleared for processing",
            FraudDecision.review: "flagged for manual review",
            FraudDecision.reject: "automatically rejected",
        }[decision]

        return (
            f"The transaction scored {score}/100 and has been {decision_text} "
            f"primarily due to: {top.reason.rstrip('.')}{others}."
        )


__all__ = ["FraudScoringService", "MODEL_VERSION"]
