"""Exhaustive tests for algorithmic error exclusion rules."""

import pytest

from src.models.neg import ErrorExclusion
from src.models.scene import Action, Entity, Position, Relation, SceneManifest
from src.models.student_profile import Discrepancy
from src.narration.error_exclusions import (
    ErrorType,
    compute_all_exclusions,
    compute_exclusions,
    filter_discrepancies,
    is_excluded,
)


# ---------------------------------------------------------------------------
# Helpers to build test entities / manifests concisely
# ---------------------------------------------------------------------------

def _entity(
    id: str,
    type: str = "rabbit",
    color: str = None,
    weight: str = None,
    temperature: str = None,
    **extra_props,
) -> Entity:
    props = {}
    if color is not None:
        props["color"] = color
    if weight is not None:
        props["weight"] = weight
    if temperature is not None:
        props["temperature"] = temperature
    props.update(extra_props)
    return Entity(id=id, type=type, properties=props, position=Position(x=100, y=130))


def _manifest(
    entities: list,
    relations: list = None,
    actions: list = None,
) -> SceneManifest:
    return SceneManifest(
        scene_id="scene_01",
        entities=entities,
        relations=relations or [],
        actions=actions or [],
    )


# ---------------------------------------------------------------------------
# Tests: ErrorType enum
# ---------------------------------------------------------------------------

class TestErrorTypeEnum:
    def test_all_values_present(self):
        expected = {
            "SPATIAL", "PROPERTY_COLOR", "PROPERTY_SIZE", "PROPERTY_WEIGHT",
            "PROPERTY_TEMPERATURE", "PROPERTY_STATE", "TEMPORAL", "IDENTITY",
            "QUANTITY", "ACTION", "RELATIONAL", "EXISTENCE", "MANNER",
            "REDUNDANCY", "OMISSION",
        }
        actual = {e.value for e in ErrorType}
        assert actual == expected

    def test_string_comparison(self):
        assert ErrorType.SPATIAL == "SPATIAL"
        assert ErrorType.PROPERTY_COLOR == "PROPERTY_COLOR"

    def test_enum_count(self):
        assert len(ErrorType) == 15


# ---------------------------------------------------------------------------
# Tests: QUANTITY exclusion (unique entity type in scene)
# ---------------------------------------------------------------------------

class TestQuantityExclusion:
    def test_unique_type_excludes_quantity(self):
        """Single rabbit in the scene -> QUANTITY excluded."""
        rabbit = _entity("rabbit_01", type="rabbit", color="brown")
        rock = _entity("rock_01", type="rock", color="grey")
        manifest = _manifest([rabbit, rock])

        exclusions = compute_exclusions(rabbit, manifest)
        assert is_excluded("rabbit_01", "QUANTITY", exclusions)

    def test_duplicate_type_does_not_exclude_quantity(self):
        """Two rabbits in the scene -> QUANTITY NOT excluded."""
        r1 = _entity("rabbit_01", type="rabbit", color="brown")
        r2 = _entity("rabbit_02", type="rabbit", color="white")
        manifest = _manifest([r1, r2])

        exclusions = compute_exclusions(r1, manifest)
        assert not is_excluded("rabbit_01", "QUANTITY", exclusions)

    def test_three_of_same_type(self):
        """Three trees -> QUANTITY not excluded for any."""
        trees = [
            _entity(f"tree_{i:02d}", type="tree", color="green")
            for i in range(1, 4)
        ]
        manifest = _manifest(trees)

        for tree in trees:
            exclusions = compute_exclusions(tree, manifest)
            assert not is_excluded(tree.id, "QUANTITY", exclusions)


# ---------------------------------------------------------------------------
# Tests: PROPERTY_COLOR exclusion (no color property)
# ---------------------------------------------------------------------------

class TestPropertyColorExclusion:
    def test_no_color_excludes(self):
        entity = _entity("wind_01", type="wind", size="large")
        manifest = _manifest([entity])

        exclusions = compute_exclusions(entity, manifest)
        assert is_excluded("wind_01", "PROPERTY_COLOR", exclusions)

    def test_with_color_does_not_exclude(self):
        entity = _entity("rabbit_01", type="rabbit", color="brown")
        manifest = _manifest([entity])

        exclusions = compute_exclusions(entity, manifest)
        assert not is_excluded("rabbit_01", "PROPERTY_COLOR", exclusions)

    def test_empty_properties(self):
        entity = Entity(
            id="thing_01", type="thing",
            properties={},
            position=Position(x=0, y=0),
        )
        manifest = _manifest([entity])

        exclusions = compute_exclusions(entity, manifest)
        assert is_excluded("thing_01", "PROPERTY_COLOR", exclusions)


# ---------------------------------------------------------------------------
# Tests: ACTION / MANNER exclusion (no action in manifest)
# ---------------------------------------------------------------------------

class TestActionMannerExclusion:
    def test_static_entity_excludes_action_and_manner(self):
        rock = _entity("rock_01", type="rock", color="grey")
        manifest = _manifest([rock], actions=[])

        exclusions = compute_exclusions(rock, manifest)
        assert is_excluded("rock_01", "ACTION", exclusions)
        assert is_excluded("rock_01", "MANNER", exclusions)

    def test_entity_with_action_does_not_exclude(self):
        rabbit = _entity("rabbit_01", type="rabbit", color="brown")
        action = Action(entity_id="rabbit_01", verb="hop", manner="quickly")
        manifest = _manifest([rabbit], actions=[action])

        exclusions = compute_exclusions(rabbit, manifest)
        assert not is_excluded("rabbit_01", "ACTION", exclusions)
        assert not is_excluded("rabbit_01", "MANNER", exclusions)

    def test_action_for_other_entity_still_excludes(self):
        """Rock has no action, even though rabbit does."""
        rabbit = _entity("rabbit_01", type="rabbit", color="brown")
        rock = _entity("rock_01", type="rock", color="grey")
        action = Action(entity_id="rabbit_01", verb="hop")
        manifest = _manifest([rabbit, rock], actions=[action])

        exclusions = compute_exclusions(rock, manifest)
        assert is_excluded("rock_01", "ACTION", exclusions)
        assert is_excluded("rock_01", "MANNER", exclusions)

        # Rabbit should NOT be excluded
        rabbit_exclusions = compute_exclusions(rabbit, manifest)
        assert not is_excluded("rabbit_01", "ACTION", rabbit_exclusions)


# ---------------------------------------------------------------------------
# Tests: PROPERTY_WEIGHT exclusion (no weight property)
# ---------------------------------------------------------------------------

class TestPropertyWeightExclusion:
    def test_no_weight_excludes(self):
        entity = _entity("rabbit_01", type="rabbit", color="brown")
        manifest = _manifest([entity])

        exclusions = compute_exclusions(entity, manifest)
        assert is_excluded("rabbit_01", "PROPERTY_WEIGHT", exclusions)

    def test_with_weight_does_not_exclude(self):
        entity = _entity("boulder_01", type="boulder", color="grey", weight="heavy")
        manifest = _manifest([entity])

        exclusions = compute_exclusions(entity, manifest)
        assert not is_excluded("boulder_01", "PROPERTY_WEIGHT", exclusions)


# ---------------------------------------------------------------------------
# Tests: PROPERTY_TEMPERATURE exclusion (no temperature property)
# ---------------------------------------------------------------------------

class TestPropertyTemperatureExclusion:
    def test_no_temperature_excludes(self):
        entity = _entity("rabbit_01", type="rabbit", color="brown")
        manifest = _manifest([entity])

        exclusions = compute_exclusions(entity, manifest)
        assert is_excluded("rabbit_01", "PROPERTY_TEMPERATURE", exclusions)

    def test_with_temperature_does_not_exclude(self):
        entity = _entity("soup_01", type="soup", color="red", temperature="hot")
        manifest = _manifest([entity])

        exclusions = compute_exclusions(entity, manifest)
        assert not is_excluded("soup_01", "PROPERTY_TEMPERATURE", exclusions)


# ---------------------------------------------------------------------------
# Tests: SPATIAL exclusion (not in any spatial relation)
# ---------------------------------------------------------------------------

class TestSpatialExclusion:
    def test_no_relation_excludes(self):
        rabbit = _entity("rabbit_01", type="rabbit", color="brown")
        manifest = _manifest([rabbit], relations=[])

        exclusions = compute_exclusions(rabbit, manifest)
        assert is_excluded("rabbit_01", "SPATIAL", exclusions)

    def test_entity_a_in_relation_does_not_exclude(self):
        rabbit = _entity("rabbit_01", type="rabbit", color="brown")
        rock = _entity("rock_01", type="rock", color="grey")
        rel = Relation(entity_a="rabbit_01", entity_b="rock_01", type="spatial", preposition="on")
        manifest = _manifest([rabbit, rock], relations=[rel])

        exclusions = compute_exclusions(rabbit, manifest)
        assert not is_excluded("rabbit_01", "SPATIAL", exclusions)

    def test_entity_b_in_relation_does_not_exclude(self):
        rabbit = _entity("rabbit_01", type="rabbit", color="brown")
        rock = _entity("rock_01", type="rock", color="grey")
        rel = Relation(entity_a="rabbit_01", entity_b="rock_01", type="spatial", preposition="on")
        manifest = _manifest([rabbit, rock], relations=[rel])

        exclusions = compute_exclusions(rock, manifest)
        assert not is_excluded("rock_01", "SPATIAL", exclusions)

    def test_uninvolved_entity_excluded(self):
        """Tree has no relation even though rabbit/rock do."""
        rabbit = _entity("rabbit_01", type="rabbit", color="brown")
        rock = _entity("rock_01", type="rock", color="grey")
        tree = _entity("tree_01", type="tree", color="green")
        rel = Relation(entity_a="rabbit_01", entity_b="rock_01", type="spatial", preposition="beside")
        manifest = _manifest([rabbit, rock, tree], relations=[rel])

        exclusions = compute_exclusions(tree, manifest)
        assert is_excluded("tree_01", "SPATIAL", exclusions)


# ---------------------------------------------------------------------------
# Tests: IDENTITY exclusion (background / decoration)
# ---------------------------------------------------------------------------

class TestIdentityExclusion:
    def test_background_type_excludes(self):
        bg = _entity("bg_01", type="background", color="blue")
        manifest = _manifest([bg])

        exclusions = compute_exclusions(bg, manifest)
        assert is_excluded("bg_01", "IDENTITY", exclusions)

    def test_decoration_type_excludes(self):
        decor = _entity("decor_01", type="decoration", color="yellow")
        manifest = _manifest([decor])

        exclusions = compute_exclusions(decor, manifest)
        assert is_excluded("decor_01", "IDENTITY", exclusions)

    def test_sky_type_excludes(self):
        sky = _entity("sky_01", type="sky")
        manifest = _manifest([sky])

        exclusions = compute_exclusions(sky, manifest)
        assert is_excluded("sky_01", "IDENTITY", exclusions)

    def test_ground_type_excludes(self):
        ground = _entity("ground_01", type="ground")
        manifest = _manifest([ground])

        exclusions = compute_exclusions(ground, manifest)
        assert is_excluded("ground_01", "IDENTITY", exclusions)

    def test_cloud_type_excludes(self):
        cloud = _entity("cloud_01", type="cloud", color="white")
        manifest = _manifest([cloud])

        exclusions = compute_exclusions(cloud, manifest)
        assert is_excluded("cloud_01", "IDENTITY", exclusions)

    def test_normal_entity_does_not_exclude_identity(self):
        rabbit = _entity("rabbit_01", type="rabbit", color="brown")
        manifest = _manifest([rabbit])

        exclusions = compute_exclusions(rabbit, manifest)
        assert not is_excluded("rabbit_01", "IDENTITY", exclusions)

    def test_case_insensitive(self):
        """Background check should be case-insensitive."""
        bg = _entity("bg_01", type="Background", color="blue")
        manifest = _manifest([bg])

        exclusions = compute_exclusions(bg, manifest)
        assert is_excluded("bg_01", "IDENTITY", exclusions)


# ---------------------------------------------------------------------------
# Tests: compute_all_exclusions
# ---------------------------------------------------------------------------

class TestComputeAllExclusions:
    def test_computes_for_all_entities(self):
        rabbit = _entity("rabbit_01", type="rabbit", color="brown")
        rock = _entity("rock_01", type="rock", color="grey")
        action = Action(entity_id="rabbit_01", verb="hop")
        rel = Relation(entity_a="rabbit_01", entity_b="rock_01", type="spatial", preposition="on")
        manifest = _manifest([rabbit, rock], relations=[rel], actions=[action])

        all_ex = compute_all_exclusions(manifest)

        # Rock should have ACTION/MANNER excluded (no action)
        assert is_excluded("rock_01", "ACTION", all_ex)
        assert is_excluded("rock_01", "MANNER", all_ex)

        # Rabbit should NOT have ACTION excluded (has hop)
        assert not is_excluded("rabbit_01", "ACTION", all_ex)

        # Both are unique types -> QUANTITY excluded for both
        assert is_excluded("rabbit_01", "QUANTITY", all_ex)
        assert is_excluded("rock_01", "QUANTITY", all_ex)

        # Both are in the spatial relation -> SPATIAL NOT excluded
        assert not is_excluded("rabbit_01", "SPATIAL", all_ex)
        assert not is_excluded("rock_01", "SPATIAL", all_ex)

    def test_empty_manifest(self):
        manifest = _manifest([])
        all_ex = compute_all_exclusions(manifest)
        assert all_ex == []


# ---------------------------------------------------------------------------
# Tests: is_excluded
# ---------------------------------------------------------------------------

class TestIsExcluded:
    def test_exact_match(self):
        ex = ErrorExclusion(entity_id="rabbit_01", excluded=["QUANTITY", "SPATIAL"], reason="test")
        assert is_excluded("rabbit_01", "QUANTITY", [ex])
        assert is_excluded("rabbit_01", "SPATIAL", [ex])

    def test_no_match_wrong_entity(self):
        ex = ErrorExclusion(entity_id="rabbit_01", excluded=["QUANTITY"], reason="test")
        assert not is_excluded("rock_01", "QUANTITY", [ex])

    def test_no_match_wrong_error_type(self):
        ex = ErrorExclusion(entity_id="rabbit_01", excluded=["QUANTITY"], reason="test")
        assert not is_excluded("rabbit_01", "SPATIAL", [ex])

    def test_empty_exclusions(self):
        assert not is_excluded("rabbit_01", "QUANTITY", [])

    def test_multiple_exclusions(self):
        exclusions = [
            ErrorExclusion(entity_id="rabbit_01", excluded=["QUANTITY"], reason="unique"),
            ErrorExclusion(entity_id="rabbit_01", excluded=["PROPERTY_WEIGHT"], reason="no weight"),
            ErrorExclusion(entity_id="rock_01", excluded=["ACTION", "MANNER"], reason="static"),
        ]
        assert is_excluded("rabbit_01", "QUANTITY", exclusions)
        assert is_excluded("rabbit_01", "PROPERTY_WEIGHT", exclusions)
        assert not is_excluded("rabbit_01", "ACTION", exclusions)
        assert is_excluded("rock_01", "ACTION", exclusions)
        assert is_excluded("rock_01", "MANNER", exclusions)
        assert not is_excluded("rock_01", "QUANTITY", exclusions)


# ---------------------------------------------------------------------------
# Tests: filter_discrepancies
# ---------------------------------------------------------------------------

class TestFilterDiscrepancies:
    def test_removes_excluded_discrepancies(self):
        exclusions = [
            ErrorExclusion(entity_id="rock_01", excluded=["ACTION", "MANNER"], reason="static"),
            ErrorExclusion(entity_id="rock_01", excluded=["QUANTITY"], reason="unique"),
        ]
        discrepancies = [
            Discrepancy(type="ACTION", entity_id="rock_01", details="wrong"),
            Discrepancy(type="PROPERTY_COLOR", entity_id="rock_01", details="missing color"),
            Discrepancy(type="QUANTITY", entity_id="rock_01", details="wrong count"),
        ]

        filtered = filter_discrepancies(discrepancies, exclusions)

        assert len(filtered) == 1
        assert filtered[0].type == "PROPERTY_COLOR"

    def test_keeps_valid_discrepancies(self):
        exclusions = [
            ErrorExclusion(entity_id="rock_01", excluded=["ACTION"], reason="static"),
        ]
        discrepancies = [
            Discrepancy(type="PROPERTY_COLOR", entity_id="rabbit_01", details="color"),
            Discrepancy(type="SPATIAL", entity_id="rabbit_01", details="spatial"),
        ]

        filtered = filter_discrepancies(discrepancies, exclusions)
        assert len(filtered) == 2

    def test_empty_discrepancies(self):
        exclusions = [
            ErrorExclusion(entity_id="rock_01", excluded=["ACTION"], reason="static"),
        ]
        filtered = filter_discrepancies([], exclusions)
        assert filtered == []

    def test_empty_exclusions(self):
        discrepancies = [
            Discrepancy(type="ACTION", entity_id="rock_01", details="wrong"),
            Discrepancy(type="SPATIAL", entity_id="rabbit_01", details="wrong"),
        ]
        filtered = filter_discrepancies(discrepancies, [])
        assert len(filtered) == 2

    def test_filters_by_entity_id(self):
        """Same error type, different entities — only the excluded one is removed."""
        exclusions = [
            ErrorExclusion(entity_id="rock_01", excluded=["QUANTITY"], reason="unique"),
        ]
        discrepancies = [
            Discrepancy(type="QUANTITY", entity_id="rock_01", details="wrong"),
            Discrepancy(type="QUANTITY", entity_id="rabbit_01", details="also wrong"),
        ]

        filtered = filter_discrepancies(discrepancies, exclusions)
        assert len(filtered) == 1
        assert filtered[0].entity_id == "rabbit_01"


# ---------------------------------------------------------------------------
# Tests: combined / realistic scenario
# ---------------------------------------------------------------------------

class TestRealisticScenario:
    def test_full_scene_exclusion_and_filtering(self):
        """Build a realistic scene and verify the whole pipeline."""
        rabbit = _entity("rabbit_01", type="rabbit", color="brown", size="small")
        rock = _entity("rock_01", type="rock", color="grey", size="large")
        sky = _entity("sky_01", type="sky")

        action = Action(entity_id="rabbit_01", verb="hop", manner="quickly")
        rel = Relation(entity_a="rabbit_01", entity_b="rock_01", type="spatial", preposition="beside")
        manifest = _manifest([rabbit, rock, sky], relations=[rel], actions=[action])

        all_ex = compute_all_exclusions(manifest)

        # Rabbit: has color, has action, has relation, unique type, no weight, no temp
        assert not is_excluded("rabbit_01", "PROPERTY_COLOR", all_ex)
        assert not is_excluded("rabbit_01", "ACTION", all_ex)
        assert not is_excluded("rabbit_01", "MANNER", all_ex)
        assert not is_excluded("rabbit_01", "SPATIAL", all_ex)
        assert is_excluded("rabbit_01", "QUANTITY", all_ex)
        assert is_excluded("rabbit_01", "PROPERTY_WEIGHT", all_ex)
        assert is_excluded("rabbit_01", "PROPERTY_TEMPERATURE", all_ex)

        # Rock: has color, no action, has relation, unique type, no weight, no temp
        assert not is_excluded("rock_01", "PROPERTY_COLOR", all_ex)
        assert is_excluded("rock_01", "ACTION", all_ex)
        assert is_excluded("rock_01", "MANNER", all_ex)
        assert not is_excluded("rock_01", "SPATIAL", all_ex)
        assert is_excluded("rock_01", "QUANTITY", all_ex)
        assert is_excluded("rock_01", "PROPERTY_WEIGHT", all_ex)

        # Sky: background, no color, no action, no relation, unique
        assert is_excluded("sky_01", "IDENTITY", all_ex)
        assert is_excluded("sky_01", "PROPERTY_COLOR", all_ex)
        assert is_excluded("sky_01", "ACTION", all_ex)
        assert is_excluded("sky_01", "SPATIAL", all_ex)
        assert is_excluded("sky_01", "QUANTITY", all_ex)

        # Now filter discrepancies
        discrepancies = [
            Discrepancy(type="PROPERTY_COLOR", entity_id="rabbit_01", details="omitted brown"),
            Discrepancy(type="QUANTITY", entity_id="rabbit_01", details="said rabbits"),
            Discrepancy(type="ACTION", entity_id="rock_01", details="said rock jumped"),
            Discrepancy(type="SPATIAL", entity_id="rabbit_01", details="wrong prep"),
            Discrepancy(type="IDENTITY", entity_id="sky_01", details="called it water"),
        ]

        filtered = filter_discrepancies(discrepancies, all_ex)

        # QUANTITY for rabbit -> excluded (unique type)
        # ACTION for rock -> excluded (no action)
        # IDENTITY for sky -> excluded (background)
        # PROPERTY_COLOR for rabbit -> kept
        # SPATIAL for rabbit -> kept
        assert len(filtered) == 2
        kept_types = {(d.entity_id, d.type) for d in filtered}
        assert ("rabbit_01", "PROPERTY_COLOR") in kept_types
        assert ("rabbit_01", "SPATIAL") in kept_types

    def test_exclusion_reasons_are_informative(self):
        """Each exclusion should have a non-empty reason string."""
        rabbit = _entity("rabbit_01", type="rabbit", color="brown")
        manifest = _manifest([rabbit])

        exclusions = compute_exclusions(rabbit, manifest)

        for ex in exclusions:
            assert len(ex.reason) > 0
            assert ex.entity_id == "rabbit_01"
