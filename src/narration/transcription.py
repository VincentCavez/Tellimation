"""Audio transcription via Google Cloud Speech-to-Text V2 (Chirp 3).

Auth: uses Application Default Credentials (ADC).
- Local dev: run `gcloud auth application-default login`
- Heroku/Railway: set GOOGLE_APPLICATION_CREDENTIALS_JSON env var
  with the service account JSON content
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import List, Optional

from google.cloud.speech_v2 import SpeechAsyncClient
from google.cloud.speech_v2.types import cloud_speech

logger = logging.getLogger(__name__)

PROJECT_ID = "tellimations-stt"
REGION = "us"
MODEL = "chirp_3"
API_ENDPOINT = f"{REGION}-speech.googleapis.com"

# On Heroku/Railway, GOOGLE_APPLICATION_CREDENTIALS_JSON contains the
# service account JSON as a string. Write it to a temp file so the
# Google client library can pick it up via GOOGLE_APPLICATION_CREDENTIALS.
_creds_json = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON")
if _creds_json and not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
    _tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    _tmp.write(_creds_json)
    _tmp.close()
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = _tmp.name
    logger.info("[transcription] Wrote service account credentials to %s", _tmp.name)


async def transcribe_audio(
    api_key: str,
    audio_bytes: bytes,
    narration_history: Optional[List[str]] = None,
    narrative_text: str = "",
) -> str:
    """Transcribe child audio to text using Chirp 3.

    Args:
        api_key: Unused (kept for signature compatibility). Auth uses ADC.
        audio_bytes: Raw audio bytes (WebM/OGG/WAV).
        narration_history: Unused (kept for signature compatibility).
        narrative_text: Unused (kept for signature compatibility).

    Returns:
        The transcription string.
    """
    client = SpeechAsyncClient(
        client_options={"api_endpoint": API_ENDPOINT},
    )

    config = cloud_speech.RecognitionConfig(
        auto_decoding_config=cloud_speech.AutoDetectDecodingConfig(),
        language_codes=["en-US"],
        model=MODEL,
    )

    request = cloud_speech.RecognizeRequest(
        recognizer=f"projects/{PROJECT_ID}/locations/{REGION}/recognizers/_",
        config=config,
        content=audio_bytes,
    )

    try:
        response = await client.recognize(request=request)
    except Exception as exc:
        logger.error("[transcription] Chirp 3 failed: %s", exc)
        return ""

    transcript = ""
    for result in response.results:
        if result.alternatives:
            transcript += result.alternatives[0].transcript

    logger.info("[transcription] Chirp 3: %r", transcript.strip())
    return transcript.strip()
