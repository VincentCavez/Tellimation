"""Dispatcher: bridge between error detection and the animation system.

Selects the highest-severity discrepancies (max 2), checks the animation
cache, and returns AnimationCommands for the narration loop to act on.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from src.models.animation_cache import AnimationCache, CachedAnimation
from src.models.neg import NEG
from src.models.student_profile import Discrepancy

MAX_CONCURRENT_ANIMATIONS = 2


class AnimationCommand(BaseModel):
    entity_id: str
    sub_entity: str
    error_type: str
    cached: bool = False
    animation: Optional[CachedAnimation] = None


def dispatch(
    discrepancies: List[Discrepancy],
    animation_cache: AnimationCache,
    entity_bounds: Dict[str, Dict[str, int]],
    scene_context: Dict[str, Any],
) -> List[AnimationCommand]:
    """Select top discrepancies and resolve animations from cache.

    Args:
        discrepancies: Detected discrepancies (already filtered by exclusions).
        animation_cache: Shared cache of generated animations.
        entity_bounds: Mapping of entity_id -> {x, y, width, height}.
        scene_context: Current scene manifest dict.

    Returns:
        Up to MAX_CONCURRENT_ANIMATIONS AnimationCommands, sorted by
        severity (highest first). Each command indicates whether the
        animation was found in cache or needs to be generated.
    """
    if not discrepancies:
        return []

    # 1. Sort by severity descending
    sorted_disc = sorted(discrepancies, key=lambda d: d.severity, reverse=True)

    # 2. Keep at most MAX_CONCURRENT_ANIMATIONS
    top = sorted_disc[:MAX_CONCURRENT_ANIMATIONS]

    # 3. Build commands, checking cache for each
    commands: List[AnimationCommand] = []
    for d in top:
        lookup_id = d.sub_entity if d.sub_entity else d.entity_id
        cached_anim = animation_cache.lookup(lookup_id, d.type)

        commands.append(AnimationCommand(
            entity_id=d.entity_id,
            sub_entity=d.sub_entity or d.entity_id,
            error_type=d.type,
            cached=cached_anim is not None,
            animation=cached_anim,
        ))

    return commands


def dispatch_hesitation(
    neg: NEG,
    satisfied_targets: List[str],
) -> Optional[AnimationCommand]:
    """Handle a hesitation event (child idle > 10s).

    Finds the highest-priority NEG target that hasn't been satisfied yet
    and returns an OMISSION animation command for it.

    Args:
        neg: Current scene's Narrative Expectation Graph.
        satisfied_targets: Target IDs already satisfied.

    Returns:
        An AnimationCommand for the highest-priority unsatisfied target,
        or None if all targets are satisfied.
    """
    satisfied_set = set(satisfied_targets)

    # Find unsatisfied targets, sorted by priority descending
    unsatisfied = [
        t for t in neg.targets if t.id not in satisfied_set
    ]
    if not unsatisfied:
        return None

    unsatisfied.sort(key=lambda t: t.priority, reverse=True)
    target = unsatisfied[0]

    return AnimationCommand(
        entity_id=target.entity_id,
        sub_entity=target.entity_id,
        error_type="OMISSION",
        cached=False,
        animation=None,
    )
