"""Audio transcription for child narration via Speech-to-Text V2 (Chirp 3).

Auth: uses Application Default Credentials (ADC).
- Local dev: run `gcloud auth application-default login`
- Heroku/Railway: set GOOGLE_APPLICATION_CREDENTIALS_JSON env var
  with the service account JSON content

## Why the adaptation is limited to child-given names

Chirp 3 accepts up to 1000 adaptation phrases, but biasing it toward *scene
content* (entity types, colours, actions) would let the recogniser silently
repair a child's genuine mistake into the correct word. `assess_corrections`
would then never fire, which destroys exactly what the study measures. So the
phrase set carries **only the names the child invented for the characters**:
those are never checked against the scene (see `_extract_character_name` and the
separate `name_assignments` branch of the correction prompt), so there is no
right answer for the bias to mask. Google's own guidance points the same way --
use as few entries as possible to avoid degrading non-adaptation terms.

## Why there is no Gemini audio fallback

A fallback for the "Chirp heard nothing" case was built and measured, then
removed: fed one second of digital silence or of low-level noise, Gemini
audio-in invents a complete, plausible child narration ("Um, okay, so then the
little blue robot walked over to the tree..."). That was reproduced on 7 of 8
prompt/thinking-budget variants, including a prompt that forbade guessing in
capitals and asked for an explicit `heard_speech` flag -- the flag came back
true on silence. An empty Chirp result usually means the child really did say
nothing, which is exactly the case the fallback gets wrong, and a fabricated
utterance would flow straight into the story, the MISL counts and the study log.
The browser sends WebM/Opus, which cannot be energy-gated in process without a
decoder, so there is no reliable way to call the model only on real speech.
An unheard turn is therefore answered by the repair prompt in
`_handle_empty_transcription`, not by a second model.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from typing import Iterable, List, Optional

from google.cloud.speech_v2 import SpeechAsyncClient
from google.cloud.speech_v2.types import cloud_speech

logger = logging.getLogger(__name__)

PROJECT_ID = "tellimations-stt"
REGION = "us"
MODEL = "chirp_3"
API_ENDPOINT = f"{REGION}-speech.googleapis.com"

# Boost applied to child-given character names. Measured across 0, 1, 2, 5, 10
# and 20 against the same audio: every value behaves identically, so this is not
# a tuning knob -- what matters is whether a phrase set is present at all. Left
# at a mid value for readability.
NAME_BOOST = 10.0

# Keep the phrase set small on purpose -- see the module docstring.
MAX_ADAPTATION_PHRASES = 50

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_names(known_names: Optional[Iterable[str]]) -> List[str]:
    """Normalise the child-given names into a small, unique phrase list."""
    if not known_names:
        return []
    seen = set()
    out: List[str] = []
    for raw in known_names:
        name = (raw or "").strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        out.append(name)
        if len(out) >= MAX_ADAPTATION_PHRASES:
            break
    return out


def _build_adaptation(names: List[str]) -> Optional[cloud_speech.SpeechAdaptation]:
    """Inline phrase set biasing Chirp toward the child's invented names."""
    if not names:
        return None
    return cloud_speech.SpeechAdaptation(
        phrase_sets=[
            cloud_speech.SpeechAdaptation.AdaptationPhraseSet(
                inline_phrase_set=cloud_speech.PhraseSet(
                    phrases=[
                        cloud_speech.PhraseSet.Phrase(value=n, boost=NAME_BOOST)
                        for n in names
                    ]
                )
            )
        ]
    )


# ---------------------------------------------------------------------------
# Chirp 3 (primary)
# ---------------------------------------------------------------------------

async def _transcribe_chirp(audio_bytes: bytes, names: List[str]) -> str:
    client = SpeechAsyncClient(client_options={"api_endpoint": API_ENDPOINT})

    config = cloud_speech.RecognitionConfig(
        auto_decoding_config=cloud_speech.AutoDetectDecodingConfig(),
        language_codes=["en-US"],
        model=MODEL,
        adaptation=_build_adaptation(names),
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

    transcript = transcript.strip()
    logger.info(
        "[transcription] Chirp 3 (%d name hints): %r", len(names), transcript
    )
    return transcript


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def _is_bare_name(transcript: str, names: List[str]) -> bool:
    """True when the whole transcript is nothing but one of the boosted names."""
    stripped = re.sub(r"[^\w\s]", "", transcript).strip().lower()
    return stripped in {n.lower() for n in names}


async def transcribe_audio(
    audio_bytes: bytes,
    known_names: Optional[Iterable[str]] = None,
) -> str:
    """Transcribe child audio to text.

    Args:
        audio_bytes: Raw audio bytes (WebM/OGG/WAV).
        known_names: Character names the child has already given, used as
            recognition hints. Nothing else about the scene is passed, on
            purpose -- see the module docstring.

    Returns:
        The transcription, or "" when nothing intelligible was heard. Callers
        must treat "" as "say so to the child", never as "skip the turn".
    """
    names = _clean_names(known_names)
    transcript = await _transcribe_chirp(audio_bytes, names)

    # A phrase set makes Chirp emit a boosted name over audio that holds no
    # speech at all: one second of digital silence transcribes as "Zoubi".
    # Measured at every boost from 0 to 20, so it cannot be tuned out. When the
    # whole transcript is just a boosted name, re-run without the phrase set to
    # tell the two cases apart -- real speech still yields something unadapted
    # (a child saying only "Zoubi" comes back as "Zubi"), silence yields "".
    if names and transcript and _is_bare_name(transcript, names):
        logger.info(
            "[transcription] %r is a bare name hint — confirming without adaptation",
            transcript,
        )
        if not await _transcribe_chirp(audio_bytes, []):
            logger.info("[transcription] Unadapted pass heard nothing — discarding %r", transcript)
            return ""

    return transcript
