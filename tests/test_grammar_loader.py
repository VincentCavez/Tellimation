"""Tests for the animation grammar loader validation (animations/grammar.py)."""

import copy
import json
from pathlib import Path

import pytest

from animations.grammar import (
    GRAMMAR_DIR,
    _validate_definition,
    get_all_animations,
    get_animation,
    reload_grammar,
)


@pytest.fixture(scope="module")
def base_def():
    """A valid definition to mutate — the real I2 (Silhouette)."""
    return json.loads((GRAMMAR_DIR / "I2.json").read_text())


class TestGrammarLoads:
    def test_all_definitions_load(self):
        reload_grammar()
        assert len(get_all_animations()) == 25

    def test_replaced_animations(self):
        assert get_animation("I2").name == "Silhouette"
        assert get_animation("C3").name == "Missing Piece"
        assert get_animation("S1").name == "Peek"

    def test_no_legacy_names_in_grammar(self):
        names = {a.name for a in get_all_animations().values()}
        assert not names & {"Nametag", "Ghost Outline", "Reveal"}


class TestCrossCheck:
    def test_valid_definition_passes(self, base_def):
        _validate_definition(base_def, Path("I2.json"))

    def test_template_referencing_undeclared_param_fails(self, base_def):
        bad = copy.deepcopy(base_def)
        bad["code_template"] = "silhouette({target}, {holdMs}, {ghostParam})"
        with pytest.raises(ValueError, match="undeclared"):
            _validate_definition(bad, Path("I2.json"))

    def test_target_placeholder_is_exempt(self, base_def):
        # {target} is filled by the engine, never declared as a parameter
        _validate_definition(base_def, Path("I2.json"))


class TestParamTypeValidation:
    def test_rgb_vary_default_out_of_bounds_fails(self, base_def):
        bad = copy.deepcopy(base_def)
        bad["parameters"][3] = {
            "name": "silhouetteColor", "type": "rgb_vary",
            "range": [0, 255], "default": [300, 15, 25],
        }
        with pytest.raises(ValueError, match=r"\[0, 255\]"):
            _validate_definition(bad, Path("I2.json"))

    def test_rgb_signed_delta_is_legal(self, base_def):
        # Plain rgb may carry signed deltas (the emanation tints)
        ok = copy.deepcopy(base_def)
        ok["parameters"][3] = {
            "name": "silhouetteColor", "type": "rgb",
            "range": [0, 255], "default": [80, -40, -80],
        }
        _validate_definition(ok, Path("I2.json"))

    def test_rgb_wrong_arity_fails(self, base_def):
        bad = copy.deepcopy(base_def)
        bad["parameters"][3] = {
            "name": "silhouetteColor", "type": "rgb",
            "range": [0, 255], "default": [15, 25],
        }
        with pytest.raises(ValueError, match="3 ints"):
            _validate_definition(bad, Path("I2.json"))

    def test_enum_default_outside_values_fails(self):
        bad = json.loads((GRAMMAR_DIR / "S1.json").read_text())
        for p in bad["parameters"]:
            if p["name"] == "hingeSide":
                p["default"] = "top"
        with pytest.raises(ValueError, match="not in range"):
            _validate_definition(bad, Path("S1.json"))

    def test_enum_empty_range_fails(self):
        bad = json.loads((GRAMMAR_DIR / "S1.json").read_text())
        for p in bad["parameters"]:
            if p["name"] == "hingeSide":
                p["range"] = []
        with pytest.raises(ValueError, match="empty range"):
            _validate_definition(bad, Path("S1.json"))


class TestEscalationDirection:
    """load_animation_params pushes int/float 40% toward the range MAX on
    non-resolution — so for every numeric param of the replaced animations,
    max must mean MORE salient. This is a schema-level invariant."""

    def test_replaced_animations_have_no_inverted_numeric_params(self):
        # If a param whose max would REDUCE salience is ever added, list it
        # here explicitly with a justification. flapMin was replaced by
        # openness for exactly this reason.
        for aid in ("I2", "C3", "S1"):
            for p in get_animation(aid).parameters:
                assert p.name != "flapMin", "flapMin escalates the wrong way; use openness"
