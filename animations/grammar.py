"""Animation grammar loader.

Loads all JSON animation definitions from animations/grammar/ and provides
lookup functions by ID, category, and mode.

The grammar is fixed at 20 animations. The LLM selects and parameterizes
animations but never creates new ones.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

GRAMMAR_DIR = Path(__file__).parent / "grammar"

REQUIRED_FIELDS = {"id", "name", "category", "mode", "scaffolding_intent",
                   "misl_elements", "target_type", "parameters", "code_template"}

VALID_CATEGORIES = {
    "Identity", "Count", "Property", "Action",
    "Space", "Time", "Relation", "Discourse",
}

VALID_MODES = {"correction", "suggestion", "both"}

VALID_TARGET_TYPES = {"entity", "duo", "group", "scene"}

VALID_PARAM_TYPES = {"int", "float", "enum", "rgb", "rgb_vary", "bool", "string", "string_array"}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class AnimationParameter(BaseModel):
    """A single parameter in an animation definition."""
    name: str
    type: str
    range: Any = Field(default_factory=list)
    default: Any = None
    description: str = ""


class AnimationDef(BaseModel):
    """A formal JSON definition of one animation from the grammar."""
    id: str
    name: str
    category: str
    mode: str
    scaffolding_intent: str
    misl_elements: List[str] = Field(default_factory=list)
    target_type: List[str] = Field(default_factory=list)
    parameters: List[AnimationParameter] = Field(default_factory=list)
    code_template: str


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_definition(data: Dict[str, Any], filepath: Path) -> None:
    """Validate a raw JSON dict against the grammar schema. Raises ValueError."""
    missing = REQUIRED_FIELDS - set(data.keys())
    if missing:
        raise ValueError(f"{filepath.name}: missing required fields: {missing}")

    if data["category"] not in VALID_CATEGORIES:
        raise ValueError(
            f"{filepath.name}: invalid category '{data['category']}', "
            f"must be one of {VALID_CATEGORIES}"
        )

    if data["mode"] not in VALID_MODES:
        raise ValueError(
            f"{filepath.name}: invalid mode '{data['mode']}', "
            f"must be one of {VALID_MODES}"
        )

    if not isinstance(data["target_type"], list):
        raise ValueError(
            f"{filepath.name}: target_type must be a list, got {type(data['target_type']).__name__}"
        )
    for tt in data["target_type"]:
        if tt not in VALID_TARGET_TYPES:
            raise ValueError(
                f"{filepath.name}: invalid target_type '{tt}', "
                f"must be one of {VALID_TARGET_TYPES}"
            )

    if not isinstance(data["misl_elements"], list):
        raise ValueError(f"{filepath.name}: misl_elements must be a list")

    if not isinstance(data["parameters"], list):
        raise ValueError(f"{filepath.name}: parameters must be a list")

    for i, param in enumerate(data["parameters"]):
        if not isinstance(param, dict):
            raise ValueError(f"{filepath.name}: parameter {i} must be a dict")
        if "name" not in param or "type" not in param:
            raise ValueError(f"{filepath.name}: parameter {i} missing 'name' or 'type'")
        if param["type"] not in VALID_PARAM_TYPES:
            raise ValueError(
                f"{filepath.name}: parameter '{param['name']}' has invalid type "
                f"'{param['type']}', must be one of {VALID_PARAM_TYPES}"
            )


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

_grammar: Dict[str, AnimationDef] = {}
_loaded = False


def _load_grammar() -> None:
    """Load all JSON files from the grammar directory."""
    global _grammar, _loaded

    if not GRAMMAR_DIR.is_dir():
        logger.warning("[grammar] Grammar directory not found: %s", GRAMMAR_DIR)
        _loaded = True
        return

    _grammar = {}
    for filepath in sorted(GRAMMAR_DIR.glob("*.json")):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)

            _validate_definition(data, filepath)

            params = [AnimationParameter(**p) for p in data["parameters"]]
            definition = AnimationDef(
                id=data["id"],
                name=data["name"],
                category=data["category"],
                mode=data["mode"],
                scaffolding_intent=data["scaffolding_intent"],
                misl_elements=data["misl_elements"],
                target_type=data["target_type"],
                parameters=params,
                code_template=data["code_template"],
            )
            _grammar[definition.id] = definition

        except (json.JSONDecodeError, ValueError) as exc:
            logger.error("[grammar] Failed to load %s: %s", filepath.name, exc)
            raise

    logger.info("[grammar] Loaded %d animation definitions", len(_grammar))
    _loaded = True


def _ensure_loaded() -> None:
    if not _loaded:
        _load_grammar()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_animation(animation_id: str) -> Optional[AnimationDef]:
    """Look up an animation definition by its ID (e.g. 'I1', 'P2')."""
    _ensure_loaded()
    return _grammar.get(animation_id)


def get_animations_by_category(category: str) -> List[AnimationDef]:
    """Return all animation definitions in a given category."""
    _ensure_loaded()
    return [d for d in _grammar.values() if d.category == category]


def get_animations_by_mode(mode: str) -> List[AnimationDef]:
    """Return all animation definitions matching a mode ('correction', 'suggestion', or 'both').

    Animations with mode='both' are included when querying either
    'correction' or 'suggestion'.
    """
    _ensure_loaded()
    results = []
    for d in _grammar.values():
        if d.mode == mode or d.mode == "both":
            results.append(d)
    return results


def get_all_animations() -> Dict[str, AnimationDef]:
    """Return the full grammar dictionary (id → AnimationDef)."""
    _ensure_loaded()
    return dict(_grammar)


def reload_grammar() -> None:
    """Force reload of all grammar files. Useful for testing."""
    global _loaded
    _loaded = False
    _load_grammar()
