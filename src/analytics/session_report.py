"""Post-session analytics: SLP report generation via Gemini 3.1 Pro."""

from __future__ import annotations

import json
import re
from typing import Any, Dict

from google import genai
from google.genai import types

from src.models.student_profile import StudentProfile

MODEL_ID = "gemini-3.1-pro-preview"

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

REPORT_SYSTEM_PROMPT = """\
You are a speech-language pathology (SLP) analyst for Tellimations, a \
research system that scaffolds children's narrative skills (ages 7-11) \
through interactive pixel-art storytelling.

You will receive a complete session log and the child's cumulative \
student profile. Produce a detailed **Markdown report** structured \
with the following sections:

# Session Report

## 1. Recurring Error Patterns
Identify the most frequent error types and describe how they \
manifested across scenes. Note any clusters (e.g. the child \
consistently omits color descriptors but handles spatial \
prepositions well). Reference specific scenes and entities.

## 2. Animation Effectiveness
For each animation type that fired during the session, report:
- How many times it fired
- How many times the child **corrected** after seeing it
- The **correction rate** (corrections / firings)
- Whether the animation appears effective, partially effective, \
  or ineffective for this child
Group by error type (PROPERTY_COLOR, SPATIAL, OMISSION, etc.).

## 3. SKILL Progress (Scene by Scene)
For each scene, summarize:
- Scene description (brief)
- Error types encountered
- Scene progress achieved
- Satisfied narrative targets
Show the trajectory — is the child improving, stable, or regressing?

## 4. Student-Profile Adaptation Impact
Analyse how the child's error profile influenced the generated \
scenes. Were later scenes richer in the child's weak areas? Did \
the adaptation help? Provide concrete examples.

## 5. Recommendations for Next Session
Based on the data, recommend:
- Which SKILL objectives to prioritise
- Which error types need more scaffolding
- Suggested scene complexity adjustments
- Any animation types that should be changed or emphasised

Use **concrete numbers** from the session log. Be specific, \
clinical, and constructive. The audience is an SLP professional.

Output **only** the Markdown report — no JSON wrapper, no \
code fences around the whole report.
"""

REPORT_USER_PROMPT = """\
# Session Log

```json
{session_log_json}
```

# Student Profile (end of session)

```json
{student_profile_json}
```

Generate the SLP session report.
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def generate_report(
    api_key: str,
    session_log: Dict[str, Any],
    student_profile: StudentProfile,
) -> str:
    """Generate a post-session SLP report via Gemini 3 Flash.

    Args:
        api_key: Gemini API key.
        session_log: Full session log dict containing scenes, utterances,
            discrepancies, animations fired, and outcomes.
        student_profile: The child's cumulative error profile at end of session.

    Returns:
        The SLP report as a Markdown string.
    """
    user_prompt = REPORT_USER_PROMPT.format(
        session_log_json=json.dumps(session_log, indent=2),
        student_profile_json=json.dumps(student_profile.model_dump(), indent=2),
    )

    client = genai.Client(api_key=api_key)
    response = await client.aio.models.generate_content(
        model=MODEL_ID,
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=REPORT_SYSTEM_PROMPT,
            thinking_config=types.ThinkingConfig(thinking_budget=1024),
            temperature=0.4,
        ),
    )

    report = response.text.strip()

    # Strip any wrapping markdown code fences the LLM may add
    fence_match = re.match(
        r"^```(?:markdown|md)?\s*\n(.*?)\n\s*```$", report, re.DOTALL
    )
    if fence_match:
        report = fence_match.group(1).strip()

    return report
