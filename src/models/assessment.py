"""Assessment decision model for the discrepancy assessment module."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


class AssessmentDecision(BaseModel):
    """Decision output from the discrepancy assessment module.

    Actions:
      - animate: play a tellimation on target_id to scaffold the child
      - oral_guidance: speak guidance_text via TTS
      - next_scene: NEG coverage is sufficient, advance the story
      - wait: no intervention needed (child is still speaking, etc.)
    """

    action: Literal["animate", "oral_guidance", "next_scene", "wait"]
    target_id: Optional[str] = None
    guidance_text: Optional[str] = None
    reasoning: str = ""
