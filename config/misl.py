"""MISL rubric — Monitoring Indicators of Scholarly Language (Gillam & Gillam, 2010).

Single source of truth for the entire pipeline: scene generation, NEG design,
transcription analysis, animation selection, and post-session analytics.
"""

from __future__ import annotations

from typing import Dict, List

# ============================================================================
# Macrostructure (7 elements, scores 0-3)
# ============================================================================

MACROSTRUCTURE = {
    "character": {
        "label": "Character (CH)",
        "scores": {
            0: "Ambiguous pronouns",
            1: "Non-specific label with determiner ('the boy')",
            2: "Proper noun ('Charles')",
            3: "Multiple characters with proper nouns",
        },
    },
    "setting": {
        "label": "Setting (S)",
        "scores": {
            0: "No reference",
            1: "General place or time",
            2: "Specific place/time related to story",
            3: "2+ specific references",
        },
    },
    "initiating_event": {
        "label": "Initiating Event (IE)",
        "scores": {
            0: "Not stated",
            1: "Stated but doesn't motivate action",
            2: "One IE that elicits active response",
            3: "2+ IEs (complex episode)",
        },
    },
    "internal_response": {
        "label": "Internal Response (IR)",
        "scores": {
            0: "No feelings",
            1: "Feelings not related to IE",
            2: "Feelings explicitly related to IE",
            3: "2+ feelings related to IE",
        },
    },
    "plan": {
        "label": "Plan (P)",
        "scores": {
            0: "No planning words",
            1: "Planning words not related to IE",
            2: "Plan directly tied to IE by main character",
            3: "Multiple plans tied to IE",
        },
    },
    "action": {
        "label": "Action (A)",
        "scores": {
            0: "No actions",
            1: "Descriptive actions not linked to IE",
            2: "Actions clearly linked to IE",
            3: "Complicating action that impedes response to IE",
        },
    },
    "consequence": {
        "label": "Consequence (CO)",
        "scores": {
            0: "No outcome",
            1: "Consequence linked to action not IE",
            2: "One consequence directly linked to IE",
            3: "2+ consequences linked to IEs",
        },
    },
}

# ============================================================================
# Microstructure (8 elements, scores 0-3)
# ============================================================================

MICROSTRUCTURE = {
    "coordinating_conjunctions": {
        "label": "Coordinating Conjunctions (CC)",
        "scores": {
            0: "None",
            1: "One (and, but, so...)",
            2: "Two different",
            3: "Three+ different",
        },
    },
    "subordinating_conjunctions": {
        "label": "Subordinating Conjunctions (SC)",
        "scores": {
            0: "None",
            1: "One (when, because, after...)",
            2: "Two different",
            3: "Three+ different",
        },
    },
    "mental_verbs": {
        "label": "Mental Verbs (M)",
        "scores": {
            0: "None",
            1: "One (thought, decided, wanted...)",
            2: "Two different",
            3: "Three+ different",
        },
    },
    "linguistic_verbs": {
        "label": "Linguistic Verbs (L)",
        "scores": {
            0: "None",
            1: "One (said, told, yelled...)",
            2: "Two different",
            3: "Three+ different",
        },
    },
    "adverbs": {
        "label": "Adverbs (ADV)",
        "scores": {
            0: "None",
            1: "One (suddenly, slowly, very...)",
            2: "Two different",
            3: "Three+ different",
        },
    },
    "elaborated_noun_phrases": {
        "label": "Elaborated Noun Phrases (ENP)",
        "scores": {
            0: "No elaboration",
            1: "One modifier before noun",
            2: "Two different modifiers",
            3: "Three+ modifiers",
        },
    },
    "grammaticality": {
        "label": "Grammaticality (G)",
        "scores": {
            0: "3+ errors",
            1: "2 errors",
            2: "1 error",
            3: "No errors",
        },
    },
    "tense": {
        "label": "Tense (T)",
        "scores": {
            0: "3+ tense changes",
            1: "2 changes",
            2: "1 change",
            3: "No changes",
        },
    },
}

# ============================================================================
# Developmental trajectory (modal scores by age, macrostructure only)
# ============================================================================

AGE_EXPECTATIONS: Dict[int, Dict[str, int]] = {
    7:  {"character": 1, "setting": 1, "initiating_event": 2, "internal_response": 0, "plan": 0, "action": 2, "consequence": 0},
    8:  {"character": 1, "setting": 1, "initiating_event": 2, "internal_response": 0, "plan": 0, "action": 2, "consequence": 0},
    9:  {"character": 1, "setting": 1, "initiating_event": 2, "internal_response": 0, "plan": 0, "action": 2, "consequence": 2},
    10: {"character": 3, "setting": 1, "initiating_event": 2, "internal_response": 0, "plan": 2, "action": 2, "consequence": 2},
    11: {"character": 3, "setting": 1, "initiating_event": 2, "internal_response": 0, "plan": 2, "action": 2, "consequence": 2},
    12: {"character": 3, "setting": 1, "initiating_event": 2, "internal_response": 2, "plan": 2, "action": 2, "consequence": 3},
    13: {"character": 3, "setting": 1, "initiating_event": 3, "internal_response": 0, "plan": 2, "action": 2, "consequence": 3},
    14: {"character": 3, "setting": 1, "initiating_event": 3, "internal_response": 0, "plan": 2, "action": 2, "consequence": 3},
    15: {"character": 3, "setting": 1, "initiating_event": 2, "internal_response": 2, "plan": 2, "action": 2, "consequence": 2},
}

# Microstructure: no empirical trajectory.
# Assumed: level 1 expected from age 6, level 2 from age 9.
MICRO_AGE_THRESHOLD_LEVEL1 = 6
MICRO_AGE_THRESHOLD_LEVEL2 = 9

# ============================================================================
# Animation categories and IDs
# ============================================================================

ANIMATION_IDS = {
    # I = Identity
    "I1_spotlight": "Scene darkens, target entity pulses gently with luminous halo",
    "I2_nametag": "Floating label with '...' attached to entity, invites naming",
    # P = Property
    "P1_color_pop": "Desaturation of everything except target to emphasize visual attributes",
    "P2a_emanation_shame": "Shame/embarrassment particles",
    "P2b_emanation_cold": "Cold/frost particles",
    "P2c_emanation_joy": "Joy/sparkle particles",
    "P2d_emanation_love": "Heart/love particles",
    "P2e_emanation_anger": "Anger particles",
    "P2f_emanation_fear": "Fear/worry particles",
    # A = Action
    "A1_motion_line": "Directional speed streaks showing direction and speed",
    "A2_flip": "Entity compresses and lurches forward, freezes mid-motion",
    # S = Space
    "S1_reveal": "Occluding layer becomes semi-transparent to show hidden elements",
    "S2_stamp": "Entity lifts slowly revealing a black silhouette, snaps back elastically, cracks radiate at impact",
    # T = Time
    "T1_flashback": "Target desaturates briefly (palette swap to grey) then re-saturates",
    "T2_timelapse": "Day-night cycle effect signaling temporal progression",
    # R = Relation
    "R1_magnetism": "Magnet sprites appear, elements drift toward each other",
    "R2_repel": "Two elements push apart from each other like same-polarity magnets",
    "R3_causal_push": "Element A rushes toward B + impact burst at collision",
    # C = Count
    "C1_sequential_glow": "Objects glow in sequence creating visual count",
    "C2_disintegration": "Entity pixelates progressively then dissolves into particles",
    "C3_ghost_outline": "Amorphous shape with '?' dissolves to nothing, scaffolds absence",
    # D = Discourse
    "D1_speech_bubble": "Pixelated speech bubble with '...' or keyword",
    "D2_thought_bubble": "Pixelated thought bubble with '...' or symbol",
    "D3_alert": "'!' sprite above entity, signals important event or reaction",
    "D4_interjection": "Comic-style burst displaying problematic word with '?'",
}

# ============================================================================
# MISL → eligible animations mapping
# ============================================================================

MISL_TO_ANIMATIONS: Dict[str, List[str]] = {
    # Macrostructure
    "character":         ["I1_spotlight", "I2_nametag"],
    "setting":           ["S1_reveal", "S2_stamp", "T2_timelapse"],
    "initiating_event":  ["A1_motion_line", "A2_flip", "R3_causal_push", "D3_alert"],
    "internal_response": ["P2a_emanation_shame", "P2b_emanation_cold", "P2c_emanation_joy", "P2d_emanation_love", "P2e_emanation_anger", "P2f_emanation_fear", "D2_thought_bubble", "D3_alert"],
    "plan":              ["D2_thought_bubble"],
    "action":            ["A1_motion_line", "A2_flip"],
    "consequence":       ["R3_causal_push"],
    # Microstructure
    "coordinating_conjunctions":  ["R1_magnetism", "R2_repel", "R3_causal_push"],
    "subordinating_conjunctions": ["R3_causal_push"],
    "mental_verbs":               ["D2_thought_bubble"],
    "linguistic_verbs":           ["D1_speech_bubble"],
    "adverbs":                    ["P1_color_pop", "P2a_emanation_shame", "P2b_emanation_cold", "P2c_emanation_joy", "P2d_emanation_love", "P2e_emanation_anger", "P2f_emanation_fear"],
    "elaborated_noun_phrases":    ["P1_color_pop", "P2a_emanation_shame", "P2b_emanation_cold", "P2c_emanation_joy", "P2d_emanation_love", "P2e_emanation_anger", "P2f_emanation_fear"],
    "grammaticality":             ["D4_interjection"],
    "tense":                      ["T1_flashback", "T2_timelapse", "D4_interjection"],
}

# Count animations apply to any element when the problem is incorrect count.
COUNT_ANIMATIONS: List[str] = ["C1_sequential_glow", "C2_disintegration", "C3_ghost_outline"]

# ============================================================================
# All MISL element keys (convenience)
# ============================================================================

MACRO_KEYS: List[str] = list(MACROSTRUCTURE.keys())
MICRO_KEYS: List[str] = list(MICROSTRUCTURE.keys())
ALL_KEYS: List[str] = MACRO_KEYS + MICRO_KEYS

# ============================================================================
# Animation ID → template name mapping
# ============================================================================

ANIMATION_ID_TO_TEMPLATE: Dict[str, str] = {
    "I1_spotlight": "spotlight",
    "I2_nametag": "nametag",
    "P1_color_pop": "color_pop",
    "P2a_emanation_shame": "emanation_shame",
    "P2b_emanation_cold": "emanation_cold",
    "P2c_emanation_joy": "emanation_joy",
    "P2d_emanation_love": "emanation_love",
    "P2e_emanation_anger": "emanation_anger",
    "P2f_emanation_fear": "emanation_fear",
    "A1_motion_line": "motion_lines",
    "A2_flip": "flip",
    "S1_reveal": "reveal",
    "S2_stamp": "stamp",
    "T1_flashback": "flashback",
    "T2_timelapse": "timelapse",
    "R1_magnetism": "magnetism",
    "R2_repel": "repel",
    "R3_causal_push": "causal_push",
    "C1_sequential_glow": "sequential_glow",
    "C2_disintegration": "disintegration",
    "C3_ghost_outline": "ghost_outline",
    "D1_speech_bubble": "speech_bubble",
    "D2_thought_bubble": "thought_bubble",
    "D3_alert": "alert",
    "D4_interjection": "interjection",
}

# ============================================================================
# Animation parameter schemas (per template)
# ============================================================================

ANIMATION_PARAMS: Dict[str, Dict] = {
    "spotlight": {
        "dimStrength":  {"type": "float", "min": 0, "max": 1, "default": 0.7, "desc": "How much non-target pixels are dimmed"},
        "glowStrength": {"type": "float", "min": 0, "max": 1, "default": 0.35, "desc": "Glow intensity on target entity"},
        "haloColor":    {"type": "rgb", "default": [255, 240, 180], "desc": "Color of the halo around entity"},
        "maxHaloSize":  {"type": "int", "min": 5, "max": 20, "default": 14, "desc": "Maximum halo radius in pixels"},
    },
    "nametag": {
        "bgColor":     {"type": "rgb", "default": [40, 40, 55], "desc": "Label background color"},
        "textColor":   {"type": "rgb", "default": [255, 255, 200], "desc": "Label text color"},
        "stringColor": {"type": "rgb", "default": [180, 180, 180], "desc": "Connecting string color"},
    },
    "color_pop": {
        "desaturationStrength": {"type": "float", "min": 0, "max": 1, "default": 0.8, "desc": "How much non-target pixels are desaturated"},
        "saturationBoost":      {"type": "float", "min": 0, "max": 0.5, "default": 0.3, "desc": "How much to boost saturation of the active color group"},
    },
    "emanation": {
        "particleCount": {"type": "int", "min": 8, "max": 30, "default": 15, "desc": "Number of particles emitted"},
    },
    "motion_lines": {
        "direction": {"type": "enum", "values": ["left", "right", "any"], "default": "any", "desc": "Direction of motion lines"},
        "lineLength": {"type": "int", "min": 10, "max": 30, "default": 20, "desc": "Length of each motion line in pixels"},
        "amplitude":  {"type": "int", "min": 5, "max": 15, "default": 10, "desc": "Vertical wave amplitude"},
    },
    "flip": {
        "speed": {"type": "float", "min": 0.5, "max": 2.0, "default": 1.0, "desc": "Speed multiplier for the flip animation"},
    },
    "reveal": {
        "revealAlpha": {"type": "float", "min": 0.3, "max": 0.9, "default": 0.6, "desc": "Transparency level of occluding layer"},
    },
    "stamp": {
        "liftPixels": {"type": "int", "min": 10, "max": 30, "default": 22, "desc": "How many pixels the entity lifts up"},
        "crackCount":  {"type": "int", "min": 6, "max": 18, "default": 12, "desc": "Number of crack lines on impact"},
    },
    "flashback": {
        "flickerIntensity": {"type": "float", "min": 0, "max": 1, "default": 0.08, "desc": "Brightness flicker amplitude (projector effect)"},
        "scratchCount":     {"type": "int", "min": 0, "max": 5, "default": 3, "desc": "Max number of vertical scratch lines per frame"},
    },
    "timelapse": {
        "isIndoor": {"type": "bool", "default": False, "desc": "Indoor scenes use warm lighting shift instead of sky gradient"},
    },
    "magnetism": {
        "entityPrefixB": {"type": "string", "default": "", "desc": "Entity ID of the second element (attracted toward)"},
    },
    "repel": {
        "entityPrefixB": {"type": "string", "default": "", "desc": "Entity ID of the second element (repelled from)"},
        "repelPixels":   {"type": "int", "min": 5, "max": 40, "default": 20, "desc": "Maximum repulsion distance in pixels"},
    },
    "causal_push": {
        "entityPrefixB": {"type": "string", "default": "", "desc": "Entity ID of the target receiving the push"},
        "knockPixels":   {"type": "int", "min": 5, "max": 30, "default": 12, "desc": "Knockback distance on impact"},
    },
    "sequential_glow": {
        "entityPrefixes": {"type": "string_array", "default": [], "desc": "Ordered list of entity IDs to glow in sequence"},
    },
    "disintegration": {
        "driftAmount": {"type": "float", "min": 0, "max": 1, "default": 0.3, "desc": "Horizontal scatter range (fraction of entity width)"},
        "fallSpeed":   {"type": "float", "min": 0.5, "max": 2.0, "default": 1.0, "desc": "Vertical fall speed multiplier"},
    },
    "ghost_outline": {
        "puddleColor": {"type": "rgb", "default": [60, 65, 85], "desc": "Color of the dark puddle shape"},
    },
    "speech_bubble": {
        "bubbleText": {"type": "string", "default": "...", "desc": "Text to display inside the speech bubble"},
    },
    "thought_bubble": {
        "bubbleText": {"type": "string", "default": "...", "desc": "Text to display inside the thought bubble"},
    },
    "alert": {
        "markCount": {"type": "int", "min": 1, "max": 3, "default": 3, "desc": "Number of exclamation marks"},
        "color":     {"type": "rgb", "default": [255, 220, 30], "desc": "Fill color of the exclamation marks"},
    },
    "interjection": {
        "word": {"type": "string", "default": "?", "desc": "Word or symbol displayed in the starburst"},
    },
}


def build_params_prompt() -> str:
    """Format ANIMATION_PARAMS as a prompt reference for the LLM."""
    lines = ["## Animation Template Parameters\n"]
    for template, params in ANIMATION_PARAMS.items():
        # Find the animation ID for this template
        aid = next((k for k, v in ANIMATION_ID_TO_TEMPLATE.items() if v == template), template)
        lines.append(f"### `{template}` ({aid})")
        lines.append(f"  Default duration: see template registration.")
        for pname, pspec in params.items():
            if pname == "entityPrefix":
                continue  # always injected server-side
            ptype = pspec["type"]
            default = pspec["default"]
            desc = pspec.get("desc", "")
            if ptype == "float":
                lines.append(f"  - `{pname}` ({ptype}, {pspec['min']}-{pspec['max']}, default={default}): {desc}")
            elif ptype == "int":
                lines.append(f"  - `{pname}` ({ptype}, {pspec['min']}-{pspec['max']}, default={default}): {desc}")
            elif ptype == "enum":
                lines.append(f"  - `{pname}` (one of {pspec['values']}, default=\"{default}\"): {desc}")
            elif ptype == "rgb":
                lines.append(f"  - `{pname}` ([r,g,b], default={default}): {desc}")
            elif ptype == "bool":
                lines.append(f"  - `{pname}` (bool, default={default}): {desc}")
            else:
                lines.append(f"  - `{pname}` ({ptype}, default={repr(default)}): {desc}")
        lines.append("")
    return "\n".join(lines)
