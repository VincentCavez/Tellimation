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
    # I = Identity
    "I1_spotlight": "Scene darkens, target entity pulses gently with luminous halo",
    "I2_nametag": "Floating label with '...' attached to entity, invites naming",
    # P = Property
    "P1_color_pop": "Desaturation of everything except target to emphasize visual attributes",
    "P2_emanation": "2-3 particle sprites revealing non-visible properties or emotions",
    # A = Action
    "A1_motion_line": "Directional speed streaks showing direction and speed",
    "A2_anticipation": "Entity compresses and lurches forward, freezes mid-motion",
    # S = Space
    "S1_reveal": "Occluding layer becomes semi-transparent to show hidden elements",
    "S2_settle": "Object sinks into its actual position with soft bounce and shadow",
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
    "setting":           ["S1_reveal", "S2_settle", "T2_timelapse"],
    "initiating_event":  ["A1_motion_line", "A2_anticipation", "R3_causal_push", "D3_alert"],
    "internal_response": ["P2_emanation", "D2_thought_bubble", "D3_alert"],
    "plan":              ["D2_thought_bubble"],
    "action":            ["A1_motion_line", "A2_anticipation"],
    "consequence":       ["R3_causal_push"],
    # Microstructure
    "coordinating_conjunctions":  ["R1_magnetism", "R2_repel", "R3_causal_push"],
    "subordinating_conjunctions": ["R3_causal_push"],
    "mental_verbs":               ["D2_thought_bubble"],
    "linguistic_verbs":           ["D1_speech_bubble"],
    "adverbs":                    ["P1_color_pop", "P2_emanation"],
    "elaborated_noun_phrases":    ["P1_color_pop", "P2_emanation"],
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
