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
    4:  {"character": 1, "setting": 0, "initiating_event": 0, "internal_response": 0, "plan": 0, "action": 0, "consequence": 0},
    5:  {"character": 1, "setting": 1, "initiating_event": 2, "internal_response": 0, "plan": 0, "action": 0, "consequence": 0},
    6:  {"character": 1, "setting": 1, "initiating_event": 1, "internal_response": 0, "plan": 0, "action": 1, "consequence": 0},
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
    # A = Identity
    "A01_decomposition": "Entity briefly disassembles into constituent parts",
    "A02_wobble": "Gelatinous vibration for categorical instability",
    "A03_nametag": "Name label appears above entity",
    # B = Property
    "B01_color_pop": "Desaturation of everything except target to emphasize color",
    "B02_scale_strain": "Object attempts claimed size, fails, returns to actual with wobble",
    "B03_emanation": "Particle sprites showing actual property (steam, frost, sparkle, dust)",
    # C = Action
    "C01_motion_line": "Directional streaks showing actual direction and speed",
    "C02_anticipation": "Character frozen in 'about to act' pose showing potential energy",
    # D = Space
    "D01_transparency_reveal": "Occluding object becomes translucent to show spatial relationship",
    "D02_settle": "Object sinks into its actual position with soft bounce",
    # E = Time
    "E01_afterimage": "Ghost-duplicate in previous action pose fades",
    "E02_timelapse": "Visual fast-forward or rewind effect",
    # F = Relation
    "F01_magnetism": "Objects attracted/repelled showing actual relationship",
    "F02_wind": "Directional force showing connection between entities",
    "F03_causal_push": "Chain reaction showing cause-effect relationship",
    # G = Quantity
    "G01_bonk": "Character hits redundant element, correction stars appear",
    "G02_sequential_glow": "Objects glow in sequence creating visual count",
    "G03_ghost_outline": "Faint dotted outline where claimed object should be, dissolves",
    # H = Discourse
    "H01_speech_bubble": "Speech bubble appears with linguistic content",
    "H02_thought_bubble": "Thought bubble appears with mental content",
}

# ============================================================================
# MISL → eligible animations mapping
# ============================================================================

MISL_TO_ANIMATIONS: Dict[str, List[str]] = {
    # Macrostructure
    "character":         ["A01_decomposition", "A02_wobble", "A03_nametag"],
    "setting":           ["D01_transparency_reveal", "D02_settle", "E02_timelapse"],
    "initiating_event":  ["C01_motion_line", "C02_anticipation", "F03_causal_push"],
    "internal_response": ["B03_emanation", "H02_thought_bubble"],
    "plan":              ["H02_thought_bubble"],
    "action":            ["C01_motion_line", "C02_anticipation"],
    "consequence":       ["F03_causal_push"],
    # Microstructure
    "coordinating_conjunctions":  ["F01_magnetism", "F02_wind", "F03_causal_push"],
    "subordinating_conjunctions": ["F03_causal_push"],
    "mental_verbs":               ["H02_thought_bubble"],
    "linguistic_verbs":           ["H01_speech_bubble"],
    "adverbs":                    ["B01_color_pop", "B02_scale_strain", "B03_emanation"],
    "elaborated_noun_phrases":    ["B01_color_pop", "B02_scale_strain", "B03_emanation"],
    "grammaticality":             ["A02_wobble"],
    "tense":                      ["E01_afterimage", "E02_timelapse"],
}

# Quantity animations apply to any element when the problem is incorrect count.
QUANTITY_ANIMATIONS: List[str] = ["G01_bonk", "G02_sequential_glow", "G03_ghost_outline"]

# ============================================================================
# All MISL element keys (convenience)
# ============================================================================

MACRO_KEYS: List[str] = list(MACROSTRUCTURE.keys())
MICRO_KEYS: List[str] = list(MICROSTRUCTURE.keys())
ALL_KEYS: List[str] = MACRO_KEYS + MICRO_KEYS
