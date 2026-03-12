"""Post-session analytics: SLP report generation via Gemini 3 Pro."""

from __future__ import annotations

import json
import re
from typing import Any, Dict

from google import genai
from google.genai import types

from src.models.student_profile import StudentProfile

MODEL_ID = "gemini-3-pro-preview"

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

## 1. Factual Error Patterns
Identify recurring factual errors the child made (utterances that \
contradicted the scene manifest). Note patterns across scenes — \
e.g., the child consistently misidentifies entity properties or \
spatial relationships. Reference specific scenes and entities.

## 2. MISL Scaffolding Opportunities
Analyse the MISL dimensions that were suggested as opportunities \
during the session. For each dimension:
- How many times it was suggested
- Whether the child improved on that dimension in later utterances
- Which MISL developmental tier the suggestions fell into
Group by macrostructure vs microstructure.

## 3. Animation Effectiveness
For each animation type that fired during the session, report:
- How many times it fired
- How many times the child **corrected** after seeing it
- The **correction rate** (corrections / firings)
- Whether the animation appears effective, partially effective, \
  or ineffective for this child

## 4. Scene-by-Scene Progress
For each scene, summarize:
- Scene description (brief)
- Number of accepted vs rejected utterances
- MISL opportunities given (out of max 3 per scene)
- Factual errors encountered
Show the trajectory — is the child improving, stable, or regressing?

## 5. MISL Difficulty Profile Analysis
Analyse the child's persistent MISL difficulty profile:
- Which dimensions have high suggestion-to-resolution ratios (struggling)
- Which dimensions are strengths
- How the profile evolved during the session

## 6. Recommendations for Next Session
Based on the data, recommend:
- Which MISL dimensions to prioritise
- Which factual error patterns need attention
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
