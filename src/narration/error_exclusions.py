"""Algorithmic error exclusion rules — no LLM involved.

For each entity in a scene, certain error types are provably impossible
based on the manifest data. This module computes those exclusions and
provides helpers to filter discrepancies accordingly.
"""

from __future__ import annotations

from enum import Enum
from typing import List

from src.models.neg import ErrorExclusion
from src.models.scene import Entity, SceneManifest
from src.models.student_profile import Discrepancy


class ErrorType(str, Enum):
    SPATIAL = "SPATIAL"
    PROPERTY_COLOR = "PROPERTY_COLOR"
    PROPERTY_SIZE = "PROPERTY_SIZE"
    PROPERTY_WEIGHT = "PROPERTY_WEIGHT"
    PROPERTY_TEMPERATURE = "PROPERTY_TEMPERATURE"
    PROPERTY_STATE = "PROPERTY_STATE"
    TEMPORAL = "TEMPORAL"
    IDENTITY = "IDENTITY"
    QUANTITY = "QUANTITY"
    ACTION = "ACTION"
    RELATIONAL = "RELATIONAL"
    EXISTENCE = "EXISTENCE"
    MANNER = "MANNER"
    REDUNDANCY = "REDUNDANCY"
    OMISSION = "OMISSION"


# Entity types treated as background / decoration.
_BACKGROUND_TYPES = frozenset({
    "background", "decoration", "sky", "ground", "cloud", "clouds",
    "sun", "moon", "stars", "horizon",
})


def compute_exclusions(
    entity: Entity,
    manifest: SceneManifest,
) -> List[ErrorExclusion]:
    """Compute error exclusions for a single entity based on the manifest.

    Rules (from CLAUDE.md):
    - Entity is unique in scene (only one of its type) -> exclude QUANTITY
    - No color in properties -> exclude PROPERTY_COLOR
    - No action associated in manifest -> exclude MANNER, ACTION
    - No weight in properties -> exclude PROPERTY_WEIGHT
    - No temperature in properties -> exclude PROPERTY_TEMPERATURE
    - Not involved in any spatial relation -> exclude SPATIAL
    - Type is background/decoration -> exclude IDENTITY

    Returns a list of ErrorExclusion objects (may be empty if no rules apply,
    or may contain multiple exclusions each with a distinct reason).
    """
    exclusions: List[ErrorExclusion] = []

    # --- Unique entity type in scene -> exclude QUANTITY ---
    same_type_count = sum(
        1 for e in manifest.entities if e.type == entity.type
    )
    if same_type_count <= 1:
        exclusions.append(ErrorExclusion(
            entity_id=entity.id,
            excluded=[ErrorType.QUANTITY],
            reason=f"entity type '{entity.type}' is unique in the scene",
        ))

    # --- No color property -> exclude PROPERTY_COLOR ---
    if "color" not in entity.properties:
        exclusions.append(ErrorExclusion(
            entity_id=entity.id,
            excluded=[ErrorType.PROPERTY_COLOR],
            reason="entity has no color property",
        ))

    # --- No action in manifest -> exclude ACTION, MANNER ---
    has_action = any(a.entity_id == entity.id for a in manifest.actions)
    if not has_action:
        exclusions.append(ErrorExclusion(
            entity_id=entity.id,
            excluded=[ErrorType.ACTION, ErrorType.MANNER],
            reason="entity is static (no action in manifest)",
        ))

    # --- No weight property -> exclude PROPERTY_WEIGHT ---
    if "weight" not in entity.properties:
        exclusions.append(ErrorExclusion(
            entity_id=entity.id,
            excluded=[ErrorType.PROPERTY_WEIGHT],
            reason="entity has no weight property",
        ))

    # --- No temperature property -> exclude PROPERTY_TEMPERATURE ---
    if "temperature" not in entity.properties:
        exclusions.append(ErrorExclusion(
            entity_id=entity.id,
            excluded=[ErrorType.PROPERTY_TEMPERATURE],
            reason="entity has no temperature property",
        ))

    # --- Not in any spatial relation -> exclude SPATIAL ---
    has_spatial = any(
        r.entity_a == entity.id or r.entity_b == entity.id
        for r in manifest.relations
    )
    if not has_spatial:
        exclusions.append(ErrorExclusion(
            entity_id=entity.id,
            excluded=[ErrorType.SPATIAL],
            reason="entity is not involved in any spatial relation",
        ))

    # --- Background / decoration type -> exclude IDENTITY ---
    if entity.type.lower() in _BACKGROUND_TYPES:
        exclusions.append(ErrorExclusion(
            entity_id=entity.id,
            excluded=[ErrorType.IDENTITY],
            reason=f"entity type '{entity.type}' is background/decoration",
        ))

    return exclusions


def compute_all_exclusions(
    manifest: SceneManifest,
) -> List[ErrorExclusion]:
    """Compute exclusions for every entity in the manifest."""
    all_exclusions: List[ErrorExclusion] = []
    for entity in manifest.entities:
        all_exclusions.extend(compute_exclusions(entity, manifest))
    return all_exclusions


def is_excluded(
    entity_id: str,
    error_type: str,
    exclusions: List[ErrorExclusion],
) -> bool:
    """Check whether a specific error type is excluded for an entity."""
    for ex in exclusions:
        if ex.entity_id == entity_id and error_type in ex.excluded:
            return True
    return False


def filter_discrepancies(
    discrepancies: List[Discrepancy],
    exclusions: List[ErrorExclusion],
) -> List[Discrepancy]:
    """Remove discrepancies whose error type is excluded for their entity."""
    return [
        d for d in discrepancies
        if not is_excluded(d.entity_id, d.type, exclusions)
    ]
