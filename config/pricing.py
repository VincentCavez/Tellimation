"""API list prices used to meter each SHIELD visitor's free credit.

Paid-tier list prices, USD. Checked 2026-11-09 against
https://ai.google.dev/gemini-api/docs/pricing and
https://cloud.google.com/speech-to-text/pricing — update here when they change.
Gemini bills thinking tokens as output.
"""

from __future__ import annotations

# Free API credit granted to each registered visitor.
FREE_CREDIT_USD = 50.0

# model id -> (input $/1M tokens, output $/1M tokens)
GEMINI_PRICES = {
    "gemini-3-flash-preview": (0.50, 3.00),
    "gemini-3.1-pro-preview": (2.00, 12.00),    # prompts <= 200k tokens
    "gemini-2.5-flash-preview-tts": (0.50, 10.00),  # text in, audio out
}

# Unknown model: charge as the dearest one rather than for free.
GEMINI_FALLBACK_PRICE = (2.00, 12.00)

# Speech-to-Text V2 standard recognition (chirp_3), first 500k min/month.
SPEECH_USD_PER_MINUTE = 0.016
