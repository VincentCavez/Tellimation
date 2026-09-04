"""Design checks for the Study 2 counterbalancing lists (block 1 only)."""

from collections import Counter

from scripts.study2.build_lists import SCENES, TARGET_ANIMATIONS, build, verify


def test_design_has_no_violation():
    assert verify(build()) == []


def test_each_target_stimulus_seen_by_two_lists():
    views = Counter(e["stimulus_id"] for lst in build()["lists"] for e in lst["block1"] if e["role"] == "target")
    assert len(views) == 24
    assert set(views.values()) == {2}


def test_every_scene_of_every_animation_in_each_list():
    for lst in build()["lists"]:
        assert lst["block2"] == []
        pairs = {(e["animation_id"], e["scene"]) for e in lst["block1"] if e["role"] == "target"}
        assert pairs == {(a, s) for a in TARGET_ANIMATIONS for s in SCENES}
