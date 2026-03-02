"""Branch generation: produce N candidate next scenes in parallel."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from src.generation.prompts.scene_prompt import BRANCH_DIRECTIVE
from src.generation.scene_generator import generate_scene
from src.models.story_state import StoryState
from src.models.student_profile import StudentProfile

# Default branch flavor guidance per index (1-based).
_BRANCH_FLAVORS = {
    1: "Take the story in a calm, reflective direction. "
    "Introduce a peaceful new setting or a quiet moment of discovery.",
    2: "Take the story in an exciting, adventurous direction. "
    "Introduce action, a challenge, or a surprise twist.",
    3: "Take the story in a whimsical, humorous direction. "
    "Introduce something funny, magical, or unexpected.",
}

# Mapping from SKILL error types to the objective they belong to.
_ERROR_TO_OBJECTIVE = {
    "PROPERTY_COLOR": "descriptive_adjectives",
    "PROPERTY_SIZE": "descriptive_adjectives",
    "PROPERTY_WEIGHT": "descriptive_adjectives",
    "PROPERTY_TEMPERATURE": "descriptive_adjectives",
    "PROPERTY_STATE": "descriptive_adjectives",
    "SPATIAL": "spatial_prepositions",
    "RELATIONAL": "spatial_prepositions",
    "TEMPORAL": "temporal_sequences",
    "QUANTITY": "quantity",
    "ACTION": "action_verbs",
    "MANNER": "action_verbs",
}


def _build_profile_emphasis(
    student_profile: Optional[StudentProfile],
    branch_index: int,
) -> str:
    """Build profile-driven emphasis text for a branch.

    Each branch targets a different weak area so the child gets varied
    practice across the three choices.
    """
    if student_profile is None or student_profile.total_utterances == 0:
        return ""

    weak = student_profile.get_weak_areas()
    if not weak:
        return "The child is doing well overall. Maintain balanced difficulty."

    # Rotate through weak areas so each branch emphasizes a different one.
    target_error = weak[(branch_index - 1) % len(weak)]
    objective = _ERROR_TO_OBJECTIVE.get(target_error, target_error)

    trend = student_profile.error_trend.get(target_error, "unknown")
    trend_note = ""
    if trend == "increasing":
        trend_note = (
            f" The child's {target_error} errors are INCREASING — "
            "create gentle, supportive opportunities to practice."
        )
    elif trend == "decreasing":
        trend_note = (
            f" The child's {target_error} errors are decreasing — "
            "maintain practice but don't over-drill."
        )

    return (
        f"# Profile-driven emphasis for this branch\n"
        f"Focus on creating narration opportunities for the "
        f"**{objective}** skill (error type: {target_error}).{trend_note}\n"
        f"\n{student_profile.to_prompt_context()}"
    )


def _build_branch_directive(
    branch_index: int,
    total_branches: int,
    student_profile: Optional[StudentProfile],
) -> str:
    """Construct the extra prompt appended for a specific branch."""
    flavor = _BRANCH_FLAVORS.get(
        branch_index,
        "Surprise the listener with a completely original direction.",
    )
    emphasis = _build_profile_emphasis(student_profile, branch_index)

    return BRANCH_DIRECTIVE.format(
        branch_index=branch_index,
        total_branches=total_branches,
        branch_flavor=flavor,
        profile_emphasis=emphasis,
    )


async def generate_branches(
    api_key: str,
    story_state: StoryState,
    student_profile: Optional[StudentProfile] = None,
    skill_objectives: Optional[List[str]] = None,
    n_branches: int = 3,
    use_reference_images: bool = True,
    skip_masks: bool = False,
) -> List[Dict[str, Any]]:
    """Generate N candidate next scenes in parallel.

    Each branch receives a different branch directive so the three options
    offer contrasting narrative directions.  None of the branches mutate
    ``story_state`` (commit_to_state=False).

    Args:
        api_key: Gemini API key.
        story_state: Current cumulative story state.
        student_profile: Child's error profile.
        skill_objectives: SKILL objectives for the session.
        n_branches: Number of branches to generate (default 3).
        skip_masks: If True, skip mask generation for speed (thumbnails).

    Returns:
        List of scene dicts, one per branch, each containing
        narrative_text, branch_summary, manifest, neg, sprite_code,
        carried_over_entities, plus preview_entities if provided by LLM.
    """
    tasks = []
    for i in range(1, n_branches + 1):
        directive = _build_branch_directive(i, n_branches, student_profile)
        tasks.append(
            generate_scene(
                api_key=api_key,
                story_state=story_state,
                student_profile=student_profile,
                skill_objectives=skill_objectives,
                commit_to_state=False,
                extra_prompt=directive,
                use_reference_images=use_reference_images,
                skip_masks=skip_masks,
            )
        )

    results = await asyncio.gather(*tasks, return_exceptions=True)

    branches: List[Dict[str, Any]] = []
    for idx, result in enumerate(results, start=1):
        if isinstance(result, Exception):
            # Log but don't crash — return the branches that succeeded.
            import logging

            logging.getLogger(__name__).warning(
                "Branch %d failed: %s", idx, result
            )
            continue
        branches.append(result)

    return branches


async def generate_one_more(
    api_key: str,
    existing_branches: List[Dict[str, Any]],
    story_state: StoryState,
    student_profile: Optional[StudentProfile] = None,
    skill_objectives: Optional[List[str]] = None,
    use_reference_images: bool = True,
    skip_masks: bool = False,
) -> Dict[str, Any]:
    """Generate one additional branch (the "I want to see one more" button).

    Args:
        api_key: Gemini API key.
        existing_branches: Already-generated branch dicts.
        story_state: Current cumulative story state.
        student_profile: Child's error profile.
        skill_objectives: SKILL objectives for the session.
        skip_masks: If True, skip mask generation for speed.

    Returns:
        A single scene dict for the new branch.
    """
    new_index = len(existing_branches) + 1
    total = new_index  # total visible to LLM

    directive = _build_branch_directive(new_index, total, student_profile)

    return await generate_scene(
        api_key=api_key,
        story_state=story_state,
        student_profile=student_profile,
        skill_objectives=skill_objectives,
        commit_to_state=False,
        extra_prompt=directive,
        use_reference_images=use_reference_images,
        skip_masks=skip_masks,
    )
