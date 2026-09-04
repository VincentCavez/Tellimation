"""Design checks for the Study 2 counterbalancing lists."""

from collections import Counter

from scripts.study2.build_lists import SCENES, TARGET_ANIMATIONS, build, verify


def test_design_has_no_violation():
    assert verify(build()) == []


def test_each_target_stimulus_seen_by_two_lists():
    views = Counter()
    for lst in build()["lists"]:
        for e in lst["block1"] + lst["block2"]:
            if e["role"] == "target":
                views[e["stimulus_id"]] += 1
    assert len(views) == 24
    assert set(views.values()) == {2}


def test_block2_scene_rotates_and_is_absent_from_block1():
    data = build()
    for lst in data["lists"]:
        assert lst["block2_scene"] == SCENES[lst["k"]]
        b1 = {(e["animation_id"], e["scene"]) for e in lst["block1"]}
        for e in lst["block2"]:
            assert e["animation_id"] in TARGET_ANIMATIONS
            assert (e["animation_id"], e["scene"]) not in b1
