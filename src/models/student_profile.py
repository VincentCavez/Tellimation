from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, Field


class Discrepancy(BaseModel):
    type: str
    entity_id: str
    sub_entity: str = ""
    details: str = ""
    severity: float = 0.5


class StudentProfile(BaseModel):
    error_counts: Dict[str, int] = Field(default_factory=dict)
    error_trend: Dict[str, str] = Field(default_factory=dict)
    difficult_entities: List[str] = Field(default_factory=list)
    strong_areas: List[str] = Field(default_factory=list)
    scenes_completed: int = 0
    corrections_after_animation: int = 0
    total_utterances: int = 0
    animation_history: List[Dict[str, Any]] = Field(default_factory=list)

    # Per-utterance log with error context
    recent_utterances: List[Dict[str, Any]] = Field(default_factory=list)
    # Each entry: {text: str, timestamp: float, scene_id: str, errors: List[str]}

    # Animation efficacy log — tracks whether animations led to correction
    animation_efficacy: List[Dict[str, Any]] = Field(default_factory=list)
    # Each entry: {
    #   target_id: str,           # sub-entity/feature targeted
    #   animation_type: str,      # type of animation (shake, colorPop, pulse, etc.)
    #   skill_type: str,          # SKILL type (descriptive_adjective, spatial_preposition, etc.)
    #   led_to_correction: bool,  # did the child correct after the animation?
    #   escalation_level: int,    # 0=animation, 1=oral guidance, 2=explicit model
    #   timestamp: float,
    #   scene_id: str,
    # }

    _recent_errors: Dict[str, List[int]] = {}

    def model_post_init(self, __context: object) -> None:
        self._recent_errors = {}

    def record_errors(self, discrepancies: List[Discrepancy]) -> None:
        self.total_utterances += 1
        entity_hit: Dict[str, int] = {}
        for d in discrepancies:
            error_type = d.type
            self.error_counts[error_type] = self.error_counts.get(error_type, 0) + 1
            entity_hit[d.entity_id] = entity_hit.get(d.entity_id, 0) + 1
            if error_type not in self._recent_errors:
                self._recent_errors[error_type] = []
            self._recent_errors[error_type].append(1)

        for eid, count in entity_hit.items():
            if count >= 2 and eid not in self.difficult_entities:
                self.difficult_entities.append(eid)

        # Record a 0 for error types seen before but absent in this utterance
        for error_type in list(self._recent_errors.keys()):
            if not any(d.type == error_type for d in discrepancies):
                self._recent_errors[error_type].append(0)

    def update_trends(self) -> None:
        window = 5
        self.error_trend = {}
        for error_type, history in self._recent_errors.items():
            if len(history) < window:
                self.error_trend[error_type] = "insufficient_data"
                continue
            recent = history[-window:]
            older = (
                history[-2 * window : -window]
                if len(history) >= 2 * window
                else history[: len(history) - window]
            )
            if not older:
                self.error_trend[error_type] = "insufficient_data"
                continue
            recent_rate = sum(recent) / len(recent)
            older_rate = sum(older) / len(older)
            if recent_rate < older_rate - 0.15:
                self.error_trend[error_type] = "decreasing"
            elif recent_rate > older_rate + 0.15:
                self.error_trend[error_type] = "increasing"
            else:
                self.error_trend[error_type] = "stable"

        self.strong_areas = []
        if self.total_utterances >= 3:
            for error_type, count in self.error_counts.items():
                rate = count / self.total_utterances
                if rate < 0.1:
                    self.strong_areas.append(error_type)

    def record_animation(
        self, entity_id: str, error_type: str, animation_type: str
    ) -> None:
        """Record that an animation was played for a discrepancy."""
        self.animation_history.append({
            "entity_id": entity_id,
            "error_type": error_type,
            "animation_type": animation_type,
            "corrected": False,
        })

    def record_correction(self, entity_id: str, error_type: str) -> None:
        """Mark the most recent animation for this entity/error as corrected."""
        for entry in reversed(self.animation_history):
            if (
                entry["entity_id"] == entity_id
                and entry["error_type"] == error_type
                and not entry["corrected"]
            ):
                entry["corrected"] = True
                self.corrections_after_animation += 1
                break

    def get_unsuccessful_animations(self) -> List[Dict[str, Any]]:
        """Return animation entries where the child did NOT correct after."""
        return [
            entry for entry in self.animation_history
            if not entry["corrected"]
        ]

    def get_effective_animations(self, skill_type: str) -> Dict[str, float]:
        """Return efficacy scores per animation type for a given SKILL type.

        Computes ``corrections / total`` for each animation_type that has been
        used for the given skill_type, based on the ``animation_efficacy`` log.
        The Tellimation module uses these scores to choose the most effective
        animation approach for this child.

        Args:
            skill_type: SKILL type string (e.g. "descriptive_adjective",
                "spatial_preposition").

        Returns:
            Dict mapping animation_type -> efficacy score (0.0 to 1.0).
            Higher is better. Only includes types with at least one trial.
        """
        totals: Dict[str, int] = {}
        successes: Dict[str, int] = {}

        for entry in self.animation_efficacy:
            if entry.get("skill_type") != skill_type:
                continue
            atype = entry.get("animation_type", "")
            if not atype:
                continue
            totals[atype] = totals.get(atype, 0) + 1
            if entry.get("led_to_correction", False):
                successes[atype] = successes.get(atype, 0) + 1

        return {
            atype: successes.get(atype, 0) / total
            for atype, total in totals.items()
        }

    def get_ineffective_animations(self, error_type: str) -> List[str]:
        """Return animation types that did NOT lead to correction for an error type.

        Uses the legacy animation_history log for backward compatibility.

        Args:
            error_type: Error type string (e.g. "PROPERTY_COLOR").

        Returns:
            List of animation_type strings that did not lead to correction.
        """
        ineffective: List[str] = []
        for entry in self.animation_history:
            if entry["error_type"] == error_type and not entry["corrected"]:
                at = entry.get("animation_type", "")
                if at and at not in ineffective:
                    ineffective.append(at)
        return ineffective

    def get_weak_areas(self) -> List[str]:
        if self.total_utterances == 0:
            return []
        error_rates = {
            et: count / self.total_utterances
            for et, count in self.error_counts.items()
        }
        sorted_types = sorted(error_rates, key=lambda k: error_rates[k], reverse=True)
        return [et for et in sorted_types if error_rates[et] > 0.2]

    def to_prompt_context(self) -> str:
        lines = ["## Student Profile"]
        lines.append(f"Utterances so far: {self.total_utterances}")
        lines.append(f"Scenes completed: {self.scenes_completed}")
        if self.error_counts:
            lines.append(
                "Error counts: "
                + ", ".join(
                    f"{k}={v}"
                    for k, v in sorted(
                        self.error_counts.items(), key=lambda x: x[1], reverse=True
                    )
                )
            )
        weak = self.get_weak_areas()
        if weak:
            lines.append(f"Weak areas (high error rate): {', '.join(weak)}")
        if self.strong_areas:
            lines.append(
                f"Strong areas (low error rate): {', '.join(self.strong_areas)}"
            )
        if self.error_trend:
            trends = ", ".join(f"{k}: {v}" for k, v in self.error_trend.items())
            lines.append(f"Trends: {trends}")
        if self.difficult_entities:
            lines.append(
                f"Difficult entities: {', '.join(self.difficult_entities)}"
            )
        lines.append(
            f"Corrections after animation: {self.corrections_after_animation}"
        )
        # Animation effectiveness — help Gemini avoid unsuccessful patterns
        unsuccessful = self.get_unsuccessful_animations()
        if unsuccessful:
            # Group by error_type → animation_type for a compact summary
            failed_by_type: Dict[str, List[str]] = {}
            for entry in unsuccessful:
                et = entry["error_type"]
                at = entry.get("animation_type", "unknown")
                if et not in failed_by_type:
                    failed_by_type[et] = []
                if at not in failed_by_type[et]:
                    failed_by_type[et].append(at)
            lines.append("Animations that did NOT lead to correction (avoid these approaches):")
            for et, anim_types in failed_by_type.items():
                lines.append(f"  {et}: {', '.join(anim_types)}")
        return "\n".join(lines)
