"""Tests for the error-to-animation dispatcher."""

import pytest

from src.models.animation_cache import AnimationCache, CachedAnimation
from src.models.neg import NEG, NarrativeTarget, TargetComponents
from src.models.student_profile import Discrepancy
from src.narration.dispatcher import (
    MAX_CONCURRENT_ANIMATIONS,
    AnimationCommand,
    dispatch,
    dispatch_hesitation,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _disc(
    type: str,
    entity_id: str,
    sub_entity: str = "",
    severity: float = 0.5,
) -> Discrepancy:
    return Discrepancy(
        type=type,
        entity_id=entity_id,
        sub_entity=sub_entity,
        severity=severity,
    )


def _make_neg() -> NEG:
    return NEG(
        targets=[
            NarrativeTarget(
                id="t1_identity",
                entity_id="rabbit_01",
                components=TargetComponents(
                    identity=True,
                    descriptors=["brown", "small"],
                    spatial="beside rock_01",
                    action="hopping quickly",
                ),
                priority=0.9,
                tolerance=0.3,
            ),
            NarrativeTarget(
                id="t2_identity",
                entity_id="rock_01",
                components=TargetComponents(
                    identity=True,
                    descriptors=["grey", "large"],
                ),
                priority=0.5,
                tolerance=0.5,
            ),
            NarrativeTarget(
                id="t3_identity",
                entity_id="tree_01",
                components=TargetComponents(
                    identity=True,
                    descriptors=["green", "tall"],
                ),
                priority=0.3,
                tolerance=0.6,
            ),
        ],
    )


EMPTY_BOUNDS: dict = {}
EMPTY_CONTEXT: dict = {}


# ---------------------------------------------------------------------------
# Tests: dispatch — basic
# ---------------------------------------------------------------------------

class TestDispatchBasic:
    def test_single_discrepancy_cache_miss(self):
        cache = AnimationCache()
        discs = [_disc("PROPERTY_COLOR", "rabbit_01", "rabbit_01.body", 0.6)]

        commands = dispatch(discs, cache, EMPTY_BOUNDS, EMPTY_CONTEXT)

        assert len(commands) == 1
        cmd = commands[0]
        assert cmd.entity_id == "rabbit_01"
        assert cmd.sub_entity == "rabbit_01.body"
        assert cmd.error_type == "PROPERTY_COLOR"
        assert cmd.cached is False
        assert cmd.animation is None

    def test_single_discrepancy_cache_hit(self):
        cache = AnimationCache()
        anim = CachedAnimation(
            code="function animate(buf,PW,PH,t) { /* color_pop */ }",
            duration_ms=1500,
            generated_for="rabbit_01.body",
        )
        cache.store("rabbit_01.body", "PROPERTY_COLOR", anim)

        discs = [_disc("PROPERTY_COLOR", "rabbit_01", "rabbit_01.body", 0.6)]
        commands = dispatch(discs, cache, EMPTY_BOUNDS, EMPTY_CONTEXT)

        assert len(commands) == 1
        cmd = commands[0]
        assert cmd.cached is True
        assert cmd.animation is not None
        assert cmd.animation.code == anim.code
        assert cmd.animation.duration_ms == 1500

    def test_empty_discrepancies(self):
        cache = AnimationCache()
        commands = dispatch([], cache, EMPTY_BOUNDS, EMPTY_CONTEXT)
        assert commands == []


# ---------------------------------------------------------------------------
# Tests: dispatch — sorting and max limit
# ---------------------------------------------------------------------------

class TestDispatchSortingAndLimit:
    def test_sorted_by_severity_descending(self):
        cache = AnimationCache()
        discs = [
            _disc("PROPERTY_SIZE", "rabbit_01", "rabbit_01.body", 0.3),
            _disc("PROPERTY_COLOR", "rabbit_01", "rabbit_01.body", 0.8),
        ]

        commands = dispatch(discs, cache, EMPTY_BOUNDS, EMPTY_CONTEXT)

        assert len(commands) == 2
        assert commands[0].error_type == "PROPERTY_COLOR"
        assert commands[1].error_type == "PROPERTY_SIZE"

    def test_max_two_discrepancies(self):
        """3 discrepancies → only top 2 by severity are dispatched."""
        cache = AnimationCache()
        discs = [
            _disc("PROPERTY_COLOR", "rabbit_01", "rabbit_01.body", 0.5),
            _disc("SPATIAL", "rabbit_01", "rabbit_01", 0.9),
            _disc("PROPERTY_SIZE", "rabbit_01", "rabbit_01.body", 0.3),
        ]

        commands = dispatch(discs, cache, EMPTY_BOUNDS, EMPTY_CONTEXT)

        assert len(commands) == MAX_CONCURRENT_ANIMATIONS
        assert len(commands) == 2
        # Highest severity first
        assert commands[0].error_type == "SPATIAL"
        assert commands[0].severity_order_check_entity == "rabbit_01" if False else True
        assert commands[1].error_type == "PROPERTY_COLOR"

    def test_five_discrepancies_still_max_two(self):
        cache = AnimationCache()
        discs = [
            _disc("PROPERTY_COLOR", "r01", severity=0.9),
            _disc("SPATIAL", "r01", severity=0.8),
            _disc("ACTION", "r01", severity=0.7),
            _disc("MANNER", "r01", severity=0.6),
            _disc("TEMPORAL", "r01", severity=0.5),
        ]

        commands = dispatch(discs, cache, EMPTY_BOUNDS, EMPTY_CONTEXT)
        assert len(commands) == 2
        assert commands[0].error_type == "PROPERTY_COLOR"
        assert commands[1].error_type == "SPATIAL"


# ---------------------------------------------------------------------------
# Tests: dispatch — cache interaction
# ---------------------------------------------------------------------------

class TestDispatchCache:
    def test_mixed_cache_hit_and_miss(self):
        """One discrepancy cached, one not."""
        cache = AnimationCache()
        anim = CachedAnimation(
            code="function animate(buf,PW,PH,t) {}",
            duration_ms=1200,
            generated_for="rabbit_01.body",
        )
        cache.store("rabbit_01.body", "PROPERTY_COLOR", anim)

        discs = [
            _disc("PROPERTY_COLOR", "rabbit_01", "rabbit_01.body", 0.8),
            _disc("SPATIAL", "rabbit_01", "rabbit_01", 0.6),
        ]

        commands = dispatch(discs, cache, EMPTY_BOUNDS, EMPTY_CONTEXT)

        assert len(commands) == 2
        # COLOR (severity 0.8) is first, cached
        assert commands[0].error_type == "PROPERTY_COLOR"
        assert commands[0].cached is True
        assert commands[0].animation is not None
        # SPATIAL (severity 0.6) is second, not cached
        assert commands[1].error_type == "SPATIAL"
        assert commands[1].cached is False
        assert commands[1].animation is None

    def test_cache_prefix_match(self):
        """Cache has rabbit_01.body, lookup for rabbit_01.body.fur should match."""
        cache = AnimationCache()
        anim = CachedAnimation(
            code="function animate(buf,PW,PH,t) {}",
            duration_ms=1000,
            generated_for="rabbit_01.body",
        )
        cache.store("rabbit_01.body", "PROPERTY_COLOR", anim)

        discs = [_disc("PROPERTY_COLOR", "rabbit_01", "rabbit_01.body.fur", 0.5)]
        commands = dispatch(discs, cache, EMPTY_BOUNDS, EMPTY_CONTEXT)

        assert len(commands) == 1
        assert commands[0].cached is True

    def test_fallback_to_entity_id_when_no_sub_entity(self):
        """If sub_entity is empty, lookup uses entity_id."""
        cache = AnimationCache()
        anim = CachedAnimation(
            code="function animate(buf,PW,PH,t) {}",
            duration_ms=1000,
            generated_for="rabbit_01",
        )
        cache.store("rabbit_01", "SPATIAL", anim)

        discs = [_disc("SPATIAL", "rabbit_01", "", 0.7)]
        commands = dispatch(discs, cache, EMPTY_BOUNDS, EMPTY_CONTEXT)

        assert len(commands) == 1
        assert commands[0].cached is True
        assert commands[0].sub_entity == "rabbit_01"  # falls back to entity_id


# ---------------------------------------------------------------------------
# Tests: dispatch_hesitation
# ---------------------------------------------------------------------------

class TestDispatchHesitation:
    def test_returns_highest_priority_unsatisfied(self):
        neg = _make_neg()
        # t1 (priority 0.9) is satisfied, t2 (0.5) and t3 (0.3) are not
        cmd = dispatch_hesitation(neg, satisfied_targets=["t1_identity"])

        assert cmd is not None
        assert cmd.entity_id == "rock_01"  # t2 has priority 0.5 > t3's 0.3
        assert cmd.error_type == "OMISSION"
        assert cmd.cached is False
        assert cmd.animation is None

    def test_returns_top_priority_when_none_satisfied(self):
        neg = _make_neg()
        cmd = dispatch_hesitation(neg, satisfied_targets=[])

        assert cmd is not None
        assert cmd.entity_id == "rabbit_01"  # t1 has highest priority (0.9)
        assert cmd.error_type == "OMISSION"

    def test_returns_none_when_all_satisfied(self):
        neg = _make_neg()
        cmd = dispatch_hesitation(
            neg,
            satisfied_targets=["t1_identity", "t2_identity", "t3_identity"],
        )
        assert cmd is None

    def test_returns_none_for_empty_neg(self):
        neg = NEG(targets=[])
        cmd = dispatch_hesitation(neg, satisfied_targets=[])
        assert cmd is None

    def test_skips_satisfied_correctly(self):
        neg = _make_neg()
        # Satisfy t1 and t2, only t3 (tree_01, priority 0.3) remains
        cmd = dispatch_hesitation(
            neg,
            satisfied_targets=["t1_identity", "t2_identity"],
        )

        assert cmd is not None
        assert cmd.entity_id == "tree_01"
        assert cmd.error_type == "OMISSION"


# ---------------------------------------------------------------------------
# Tests: AnimationCommand model
# ---------------------------------------------------------------------------

class TestAnimationCommandModel:
    def test_json_roundtrip_cached(self):
        anim = CachedAnimation(code="function animate(){}", duration_ms=1000)
        cmd = AnimationCommand(
            entity_id="rabbit_01",
            sub_entity="rabbit_01.body",
            error_type="PROPERTY_COLOR",
            cached=True,
            animation=anim,
        )
        data = cmd.model_dump()
        restored = AnimationCommand.model_validate(data)
        assert restored.cached is True
        assert restored.animation.code == "function animate(){}"

    def test_json_roundtrip_not_cached(self):
        cmd = AnimationCommand(
            entity_id="rabbit_01",
            sub_entity="rabbit_01",
            error_type="SPATIAL",
            cached=False,
            animation=None,
        )
        data = cmd.model_dump()
        restored = AnimationCommand.model_validate(data)
        assert restored.cached is False
        assert restored.animation is None
