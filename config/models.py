"""Central registry of Gemini model IDs.

Single source of truth: every module imports its model ID from here so that a
deprecation only ever needs one edit.

Status as of 2026-08-26 (see https://ai.google.dev/gemini-api/docs/deprecations):

  gemini-3-flash-preview     deprecated, NO shutdown date announced.
                             Announced replacement: gemini-3.6-flash, which is
                             +50% input / +25% output today and ~3x from
                             2027-01-01. Its gains are on coding/agentic work,
                             which does not match our short-JSON classification
                             workload, so we stay on the preview until a
                             shutdown date is announced or a benchmark on real
                             session logs justifies a move.
  gemini-3-pro-preview       SHUT DOWN 2026-03-09 -> gemini-3.1-pro-preview.
  gemini-3.1-flash-image-preview
                             SHUT DOWN 2026-06-25 -> gemini-3.1-flash-image.
  gemini-2.5-flash-preview-tts
                             still served, no shutdown date. Replacement
                             gemini-3.1-flash-tts-preview costs 2x for no gain
                             on our low volume (escalation only).

Note: temperature / top_p / top_k were deprecated on 2026-07-21 and are no
longer passed anywhere in this codebase.
"""

from __future__ import annotations

# Workhorse: assessment, enrichment, MISL detection, resolution, short helper
# calls, oral guidance, scene generation.
FLASH_MODEL_ID = "gemini-3-flash-preview"

# Deep reasoning: post-session SLP report only.
PRO_MODEL_ID = "gemini-3.1-pro-preview"

# Image generation for the offline stimulus pipeline (Nano Banana 2).
IMAGE_MODEL_ID = "gemini-3.1-flash-image"

# Speech synthesis for voice guidance.
TTS_MODEL_ID = "gemini-2.5-flash-preview-tts"
TTS_VOICE = "Achernar"
