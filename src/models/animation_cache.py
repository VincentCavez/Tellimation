from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class CachedAnimation(BaseModel):
    code: Optional[str] = None
    template: Optional[str] = None
    params: Dict[str, Any] = Field(default_factory=dict)
    particles: List[Dict[str, Any]] = Field(default_factory=list)
    text_overlays: List[Dict[str, Any]] = Field(default_factory=list)
    duration_ms: int = 1200
    generated_for: str = ""

    def to_ws_dict(self) -> Dict[str, Any]:
        """Return the dict to send over WebSocket."""
        d: Dict[str, Any] = {"duration_ms": self.duration_ms}
        if self.template:
            d["template"] = self.template
            d["params"] = self.params
            if self.particles:
                d["particles"] = self.particles
            if self.text_overlays:
                d["text_overlays"] = self.text_overlays
        elif self.code:
            d["code"] = self.code
        return d


class AnimationCache(BaseModel):
    cache: Dict[str, Dict[str, CachedAnimation]] = Field(default_factory=dict)

    def store(
        self, entity_id: str, error_type: str, animation: CachedAnimation
    ) -> None:
        if entity_id not in self.cache:
            self.cache[entity_id] = {}
        self.cache[entity_id][error_type] = animation

    def has(self, entity_id: str, error_type: str) -> bool:
        return self.lookup(entity_id, error_type) is not None

    def lookup(self, entity_id: str, error_type: str) -> Optional[CachedAnimation]:
        # Exact match
        entry = self.cache.get(entity_id, {}).get(error_type)
        if entry is not None:
            return entry

        # Prefix: entity_id is a prefix of a cached key
        # e.g. lookup("rabbit_01", ...) matches cache["rabbit_01.body"]
        for cached_id, errors in self.cache.items():
            if cached_id.startswith(entity_id + ".") and error_type in errors:
                return errors[error_type]

        # Reverse prefix: cached key is a prefix of entity_id
        # e.g. lookup("rabbit_01.body.fur", ...) matches cache["rabbit_01.body"]
        for cached_id, errors in self.cache.items():
            if entity_id.startswith(cached_id + ".") and error_type in errors:
                return errors[error_type]

        return None
