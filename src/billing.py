"""Meter API spend per signed-in visitor.

A WebSocket handler calls `metered(participant)` once; every Gemini call
(`client.aio.models.generate_content`, patched here) and every Speech-to-Text
recognition made from that handler — including tasks it spawns, which inherit
the context — is priced from its usage report and added to the visitor's
spend in `src.accounts.store`. Calls made outside a metered context (offline
generation, anonymous sessions) are not counted.
"""

from __future__ import annotations

import contextvars
import functools
import logging
from typing import Any, Optional

from google.genai import models as _genai_models

from config.pricing import (
    GEMINI_FALLBACK_PRICE,
    GEMINI_PRICES,
    SPEECH_USD_PER_MINUTE,
)
from src.accounts import store

logger = logging.getLogger(__name__)

_participant: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "billing_participant", default=None)


def metered(participant: Optional[str]) -> None:
    """Charge API calls made from the current task (and its children) to participant."""
    _participant.set(participant)


def gemini_cost(model: str, usage: Any) -> float:
    """Return the USD cost of a Gemini call from its model name and usage metadata (0 if usage is missing)."""
    if usage is None:
        return 0.0
    price_in, price_out = GEMINI_PRICES.get(model.removeprefix("models/"), GEMINI_FALLBACK_PRICE)
    tokens_in = usage.prompt_token_count or 0
    tokens_out = (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)
    return (tokens_in * price_in + tokens_out * price_out) / 1e6


def record_speech(billed_seconds: float) -> None:
    """Charge the current participant for billed seconds of speech recognition."""
    participant = _participant.get()
    if participant and billed_seconds > 0:
        store.add_spend(participant, billed_seconds / 60 * SPEECH_USD_PER_MINUTE)


def _patch_gemini() -> None:
    """Wrap the async Gemini generate_content so each call is charged to the current participant (applied once)."""
    original = _genai_models.AsyncModels.generate_content
    if getattr(original, "_shield_metered", False):
        return

    @functools.wraps(original)
    async def generate_content(self, *args, **kwargs):
        """Call the original generate_content and bill its token usage to the current participant, if any."""
        response = await original(self, *args, **kwargs)
        participant = _participant.get()
        if participant:
            try:
                model = kwargs.get("model") or (args[0] if args else "")
                store.add_spend(participant, gemini_cost(str(model), response.usage_metadata))
            except Exception:
                logger.exception("[billing] could not meter a Gemini call")
        return response

    generate_content._shield_metered = True  # type: ignore[attr-defined]
    _genai_models.AsyncModels.generate_content = generate_content


_patch_gemini()
