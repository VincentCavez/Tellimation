import json

import pytest

from src.models.scene import Action, Entity, Position, Relation, SceneManifest
from src.models.assessment import (
    AssessmentResponse,
    FactualError,
    MISLOpportunity,
    SceneAssessmentEntry,
    SceneLog,
    SceneStoryEntry,
)
from src.models.story_state import ActiveEntity, StoryState
from src.models.student_profile import AnimationDecision, Discrepancy, StudentProfile
from src.models.animation_cache import AnimationCache, CachedAnimation


# ---------------------------------------------------------------------------
# scene.py
# ---------------------------------------------------------------------------

class TestScene:
    def test_entity_json_roundtrip(self):
        e = Entity(
            id="cat_01",
            type="cat",
            properties={"color": "orange", "size": "small"},
            position=Position(x=0.09, y=0.07, spatial_ref="on fence_01"),
            emotion="happy",
            carried_over=False,
        )
        data = json.loads(e.model_dump_json())
        assert data["id"] == "cat_01"
        assert data["properties"]["color"] == "orange"
        assert data["position"]["spatial_ref"] == "on fence_01"
        restored = Entity.model_validate(data)
        assert restored == e

    def test_manifest_get_entity(self):
        m = SceneManifest(
            scene_id="s1",
            entities=[
                Entity(id="a", type="rabbit", position=Position(x=0.0, y=0.0)),
                Entity(id="b", type="owl", position=Position(x=0.1, y=0.1)),
            ],
        )
        assert m.get_entity("a") is not None
        assert m.get_entity("a").type == "rabbit"
        assert m.get_entity("missing") is None

    def test_manifest_entity_ids(self):
        m = SceneManifest(
            scene_id="s1",
            entities=[
                Entity(id="x", type="fox", position=Position(x=0.0, y=0.0)),
                Entity(id="y", type="tree", position=Position(x=0.05, y=0.05)),
            ],
        )
        assert m.entity_ids() == ["x", "y"]

    def test_manifest_json_roundtrip(self):
        m = SceneManifest(
            scene_id="scene_01",
            entities=[
                Entity(
                    id="rabbit_01",
                    type="rabbit",
                    properties={"color": "brown"},
                    position=Position(x=0.08, y=0.14, spatial_ref="on rock_01"),
                    emotion="curious",
                    carried_over=True,
                ),
            ],
            relations=[
                Relation(
                    entity_a="rabbit_01",
                    entity_b="rock_01",
                    type="spatial",
                    preposition="on",
                ),
            ],
            actions=[
                Action(entity_id="rabbit_01", verb="hop", tense="past", manner="quickly"),
            ],
        )
        data = json.loads(m.model_dump_json())
        restored = SceneManifest.model_validate(data)
        assert restored.scene_id == "scene_01"
        assert len(restored.entities) == 1
        assert restored.entities[0].carried_over is True
        assert restored.relations[0].preposition == "on"
        assert restored.actions[0].manner == "quickly"


# ---------------------------------------------------------------------------
# assessment.py
# ---------------------------------------------------------------------------

class TestAssessment:
    def test_assessment_response_defaults(self):
        resp = AssessmentResponse()
        assert resp.factual_errors == []
        assert resp.misl_opportunities == []
        assert resp.utterance_is_acceptable is True

    def test_assessment_response_with_errors(self):
        resp = AssessmentResponse(
            factual_errors=[
                FactualError(
                    utterance_fragment="the blue cat",
                    manifest_ref="cat_01",
                    explanation="The cat is orange, not blue",
                ),
            ],
            utterance_is_acceptable=False,
        )
        assert len(resp.factual_errors) == 1
        assert resp.factual_errors[0].manifest_ref == "cat_01"
        assert resp.utterance_is_acceptable is False

    def test_assessment_response_with_opportunities(self):
        resp = AssessmentResponse(
            misl_opportunities=[
                MISLOpportunity(
                    dimension="elaborated_noun_phrases",
                    manifest_elements=["cat_01"],
                    suggestion="Can you describe what the cat looks like?",
                ),
            ],
        )
        assert len(resp.misl_opportunities) == 1
        assert resp.misl_opportunities[0].dimension == "elaborated_noun_phrases"

    def test_scene_log_tracks_assessments(self):
        log = SceneLog(scene_id="scene_01", scene_manifest={"entities": []})
        log.story.append(SceneStoryEntry(utterance_text="a cat"))
        log.assessments.append(SceneAssessmentEntry(
            utterance_text="a cat",
            accepted=True,
        ))
        assert len(log.story) == 1
        assert len(log.assessments) == 1
        assert log.misl_opportunities_given == 0

    def test_scene_log_json_roundtrip(self):
        log = SceneLog(scene_id="s1", scene_manifest={})
        log.story.append(SceneStoryEntry(utterance_text="hello"))
        data = json.loads(log.model_dump_json())
        restored = SceneLog.model_validate(data)
        assert restored.scene_id == "s1"
        assert len(restored.story) == 1


# ---------------------------------------------------------------------------
# story_state.py
# ---------------------------------------------------------------------------

class TestStoryState:
    def test_add_scene_stores_scene_and_sprites(self):
        state = StoryState(session_id="s1", participant_id="P01")
        state.add_scene(
            scene_id="scene_01",
            narrative_text="A rabbit appears.",
            manifest={"entities": []},

            sprite_code={"rabbit_01": "circ(50,50,10,255,255,255,'rabbit_01');"},
        )
        assert len(state.scenes) == 1
        assert state.scenes[0]["scene_id"] == "scene_01"
        assert "rabbit_01" in state.active_entities
        assert state.active_entities["rabbit_01"].first_appeared == "scene_01"

    def test_add_scene_updates_existing_entity_sprite(self):
        state = StoryState(session_id="s1")
        state.active_entities["rabbit_01"] = ActiveEntity(
            type="rabbit",
            sprite_code="old_code",
            first_appeared="scene_01",
        )
        state.add_scene(
            scene_id="scene_02",
            narrative_text="Rabbit moves.",
            manifest={},
            sprite_code={"rabbit_01": "new_code"},
        )
        assert state.active_entities["rabbit_01"].sprite_code == "new_code"
        # first_appeared should not change
        assert state.active_entities["rabbit_01"].first_appeared == "scene_01"

    def test_get_entity_sprite(self):
        state = StoryState()
        assert state.get_entity_sprite("nope") is None
        state.active_entities["owl_01"] = ActiveEntity(
            type="owl", sprite_code="ellip(...);"
        )
        assert state.get_entity_sprite("owl_01") == "ellip(...);"

    def test_get_entity_sprite_returns_none_for_empty_code(self):
        state = StoryState()
        state.active_entities["owl_01"] = ActiveEntity(type="owl", sprite_code="")
        assert state.get_entity_sprite("owl_01") is None

    def test_carry_over_entities(self):
        state = StoryState()
        state.active_entities["rabbit_01"] = ActiveEntity(
            type="rabbit",
            sprite_code="code_r",
            first_appeared="scene_01",
            last_position={"x": 50, "y": 50},
        )
        state.active_entities["rock_01"] = ActiveEntity(
            type="rock",
            sprite_code="code_rock",
            first_appeared="scene_01",
        )
        manifest = SceneManifest(
            scene_id="scene_02",
            entities=[
                Entity(
                    id="rabbit_01", type="rabbit",
                    position=Position(x=0.09, y=0.11),
                    carried_over=True,
                ),
                Entity(
                    id="owl_01", type="owl",
                    position=Position(x=0.18, y=0.08),
                    carried_over=False,
                ),
            ],
        )
        carried, new = state.carry_over_entities(manifest)
        assert carried == ["rabbit_01"]
        assert new == ["owl_01"]
        assert state.active_entities["rabbit_01"].last_position == {"x": 0.09, "y": 0.11}

    def test_json_roundtrip(self):
        state = StoryState(
            session_id="s1",
            participant_id="P01",
        )
        state.add_scene(
            scene_id="scene_01",
            narrative_text="Hello",
            manifest={},
            sprite_code={"cat_01": "rect(0,0,10,10,0,0,0,'cat_01');"},
        )
        data = json.loads(state.model_dump_json())
        restored = StoryState.model_validate(data)
        assert restored.session_id == "s1"
        assert len(restored.scenes) == 1
        assert "cat_01" in restored.active_entities


# ---------------------------------------------------------------------------
# student_profile.py
# ---------------------------------------------------------------------------

class TestStudentProfile:
    def _make_discrepancy(self, error_type: str, entity_id: str = "cat_01") -> Discrepancy:
        return Discrepancy(type=error_type, entity_id=entity_id, severity=0.7)

    def test_record_errors_increments_counts(self):
        p = StudentProfile()
        p.record_errors([
            self._make_discrepancy("PROPERTY_COLOR"),
            self._make_discrepancy("PROPERTY_COLOR"),
            self._make_discrepancy("SPATIAL"),
        ])
        assert p.error_counts["PROPERTY_COLOR"] == 2
        assert p.error_counts["SPATIAL"] == 1
        assert p.total_utterances == 1

    def test_record_errors_tracks_difficult_entities(self):
        p = StudentProfile()
        # Two errors on same entity in one utterance -> difficult
        p.record_errors([
            self._make_discrepancy("PROPERTY_COLOR", "cat_01"),
            self._make_discrepancy("SPATIAL", "cat_01"),
        ])
        assert "cat_01" in p.difficult_entities

    def test_record_errors_single_error_not_difficult(self):
        p = StudentProfile()
        p.record_errors([self._make_discrepancy("SPATIAL", "tree_01")])
        assert "tree_01" not in p.difficult_entities

    def test_get_weak_areas(self):
        p = StudentProfile()
        # 5 utterances, property_color in every one = rate 1.0
        for _ in range(5):
            p.record_errors([self._make_discrepancy("PROPERTY_COLOR")])
        # SPATIAL only once = rate 0.2, exactly at threshold -> excluded
        # Add one more call with spatial
        p.record_errors([self._make_discrepancy("SPATIAL")])
        weak = p.get_weak_areas()
        assert "PROPERTY_COLOR" in weak

    def test_get_weak_areas_empty_when_no_utterances(self):
        p = StudentProfile()
        assert p.get_weak_areas() == []

    def test_update_trends_insufficient_data(self):
        p = StudentProfile()
        p.record_errors([self._make_discrepancy("PROPERTY_COLOR")])
        p.update_trends()
        assert p.error_trend["PROPERTY_COLOR"] == "insufficient_data"

    def test_update_trends_stable(self):
        p = StudentProfile()
        # 10 utterances all with the same error -> stable
        for _ in range(10):
            p.record_errors([self._make_discrepancy("PROPERTY_COLOR")])
        p.update_trends()
        assert p.error_trend["PROPERTY_COLOR"] == "stable"

    def test_update_trends_decreasing(self):
        p = StudentProfile()
        # First 5 utterances: always error
        for _ in range(5):
            p.record_errors([self._make_discrepancy("PROPERTY_COLOR")])
        # Next 5 utterances: no error
        for _ in range(5):
            p.record_errors([])
        p.update_trends()
        assert p.error_trend["PROPERTY_COLOR"] == "decreasing"

    def test_update_trends_increasing(self):
        p = StudentProfile()
        # First 5: no error (but we need the type to exist first)
        p.record_errors([self._make_discrepancy("SPATIAL")])
        for _ in range(4):
            p.record_errors([])
        # Next 5: always error
        for _ in range(5):
            p.record_errors([self._make_discrepancy("SPATIAL")])
        p.update_trends()
        assert p.error_trend["SPATIAL"] == "increasing"

    def test_to_prompt_context_contains_key_info(self):
        p = StudentProfile(
            error_counts={"PROPERTY_COLOR": 5, "SPATIAL": 2},
            total_utterances=10,
            scenes_completed=3,
            corrections_after_animation=7,
            difficult_entities=["cat_01"],
        )
        ctx = p.to_prompt_context()
        assert "Student Profile" in ctx
        assert "Utterances so far: 10" in ctx
        assert "PROPERTY_COLOR=5" in ctx
        assert "cat_01" in ctx
        assert "Corrections after animation: 7" in ctx

    def test_json_roundtrip(self):
        p = StudentProfile(
            error_counts={"PROPERTY_COLOR": 3},
            error_trend={"PROPERTY_COLOR": "stable"},
            difficult_entities=["cat_01"],
            strong_areas=["QUANTITY"],
            scenes_completed=2,
            corrections_after_animation=5,
            total_utterances=10,
        )
        data = json.loads(p.model_dump_json())
        restored = StudentProfile.model_validate(data)
        assert restored.error_counts == {"PROPERTY_COLOR": 3}
        assert restored.difficult_entities == ["cat_01"]


# ---------------------------------------------------------------------------
# animation_cache.py
# ---------------------------------------------------------------------------

class TestAnimationCache:
    def test_store_and_exact_lookup(self):
        cache = AnimationCache()
        anim = CachedAnimation(
            mode="use_default", template="spotlight",
            duration_ms=1000, generated_for="cat_01.body"
        )
        cache.store("cat_01.body", "PROPERTY_COLOR", anim)
        result = cache.lookup("cat_01.body", "PROPERTY_COLOR")
        assert result is not None
        assert result.template == "spotlight"

    def test_has(self):
        cache = AnimationCache()
        cache.store("a", "X", CachedAnimation(template="spotlight"))
        assert cache.has("a", "X") is True
        assert cache.has("a", "Y") is False
        assert cache.has("b", "X") is False

    def test_lookup_miss(self):
        cache = AnimationCache()
        assert cache.lookup("cat_01", "SPATIAL") is None

    def test_prefix_match_parent_to_child(self):
        """lookup("rabbit_01", ...) should match cache["rabbit_01.body"]."""
        cache = AnimationCache()
        anim = CachedAnimation(
            mode="adjust_params", template="emanation",
            params={"particleType": "hearts"}, generated_for="rabbit_01.body"
        )
        cache.store("rabbit_01.body", "PROPERTY_COLOR", anim)
        result = cache.lookup("rabbit_01", "PROPERTY_COLOR")
        assert result is not None
        assert result.template == "emanation"
        assert result.params["particleType"] == "hearts"

    def test_prefix_match_child_to_parent(self):
        """lookup("rabbit_01.body.fur", ...) should match cache["rabbit_01.body"]."""
        cache = AnimationCache()
        anim = CachedAnimation(
            mode="use_default", template="stamp", generated_for="rabbit_01.body"
        )
        cache.store("rabbit_01.body", "SPATIAL", anim)
        result = cache.lookup("rabbit_01.body.fur", "SPATIAL")
        assert result is not None
        assert result.template == "stamp"

    def test_prefix_no_false_positive(self):
        """'rabbit_01' should NOT match 'rabbit_012' (not a dot boundary)."""
        cache = AnimationCache()
        cache.store("rabbit_012", "SPATIAL", CachedAnimation(template="reveal"))
        assert cache.lookup("rabbit_01", "SPATIAL") is None

    def test_exact_match_takes_priority(self):
        cache = AnimationCache()
        cache.store("cat_01.body", "PROPERTY_COLOR", CachedAnimation(template="color_pop"))
        cache.store("cat_01", "PROPERTY_COLOR", CachedAnimation(template="spotlight"))
        result = cache.lookup("cat_01", "PROPERTY_COLOR")
        assert result is not None
        assert result.template == "spotlight"

    def test_custom_code_mode(self):
        cache = AnimationCache()
        anim = CachedAnimation(
            mode="custom_code",
            code="function animate(buf,PW,PH,t){}",
            duration_ms=1500,
            generated_for="cat_01",
        )
        cache.store("cat_01", "IDENTITY", anim)
        result = cache.lookup("cat_01", "IDENTITY")
        assert result is not None
        assert result.mode == "custom_code"
        assert "animate" in result.code

    def test_sequence_mode(self):
        cache = AnimationCache()
        anim = CachedAnimation(
            mode="sequence",
            steps=[
                {"template": "anticipation", "params": {}, "duration_ms": 1500},
                {"template": "thought_bubble", "params": {}, "duration_ms": 1200},
            ],
            duration_ms=2700,
            generated_for="fox_01",
        )
        cache.store("fox_01", "plan", anim)
        result = cache.lookup("fox_01", "plan")
        assert result is not None
        assert result.mode == "sequence"
        assert len(result.steps) == 2

    def test_json_roundtrip(self):
        cache = AnimationCache()
        cache.store(
            "owl_01.body",
            "IDENTITY",
            CachedAnimation(
                mode="adjust_params", template="spotlight",
                params={"dimStrength": 0.9}, duration_ms=800,
                generated_for="owl_01.body",
            ),
        )
        data = json.loads(cache.model_dump_json())
        restored = AnimationCache.model_validate(data)
        assert restored.has("owl_01.body", "IDENTITY")
        result = restored.lookup("owl_01", "IDENTITY")
        assert result is not None
        assert result.params["dimStrength"] == 0.9


# ---------------------------------------------------------------------------
# AnimationDecision
# ---------------------------------------------------------------------------

class TestAnimationDecision:
    def test_defaults(self):
        d = AnimationDecision()
        assert d.mode == ""
        assert d.outcome == "pending"
        assert d.escalated_to_voice is False
        assert d.params == {}
        assert d.steps == []

    def test_use_default_decision(self):
        d = AnimationDecision(
            mode="use_default",
            animation_id="I1_spotlight",
            template="spotlight",
            target_id="cat_01",
            misl_element="character",
            duration_ms=3000,
        )
        assert d.mode == "use_default"
        assert d.template == "spotlight"

    def test_adjust_params_decision(self):
        d = AnimationDecision(
            mode="adjust_params",
            animation_id="P2_emanation",
            template="emanation",
            params={"particleType": "hearts", "particleCount": 25},
            duration_ms=2500,
        )
        assert d.params["particleType"] == "hearts"
        assert d.params["particleCount"] == 25

    def test_sequence_decision(self):
        d = AnimationDecision(
            mode="sequence",
            animation_id="A2+D2",
            steps=[
                {"template": "anticipation", "params": {}, "duration_ms": 2000},
                {"template": "thought_bubble", "params": {}, "duration_ms": 1500},
            ],
            duration_ms=3500,
        )
        assert len(d.steps) == 2
        assert d.steps[0]["template"] == "anticipation"

    def test_outcome_tracking(self):
        d = AnimationDecision(mode="use_default", outcome="pending")
        d.outcome = "success"
        assert d.outcome == "success"
        d.outcome = "failure"
        d.escalated_to_voice = True
        assert d.outcome == "failure"
        assert d.escalated_to_voice is True

    def test_json_roundtrip(self):
        d = AnimationDecision(
            mode="adjust_params",
            animation_id="P1_color_pop",
            template="color_pop",
            params={"cycleCount": 3},
            target_id="owl_01",
            misl_element="adverbs",
            duration_ms=3000,
            outcome="success",
        )
        data = json.loads(d.model_dump_json())
        restored = AnimationDecision.model_validate(data)
        assert restored.mode == "adjust_params"
        assert restored.params["cycleCount"] == 3
        assert restored.outcome == "success"

    def test_student_profile_has_decisions_list(self):
        p = StudentProfile()
        assert p.animation_decisions == []
        p.animation_decisions.append(AnimationDecision(
            mode="use_default",
            animation_id="I1_spotlight",
            template="spotlight",
            outcome="success",
        ))
        assert len(p.animation_decisions) == 1
        assert p.animation_decisions[0].outcome == "success"
