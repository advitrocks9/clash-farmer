from __future__ import annotations

from pydantic import BaseModel


class UpgradeDecision(BaseModel):
    action: str  # "upgrade_building" | "upgrade_hero" | "wait" | "skip"
    target: str  # building or hero name
    target_level: int
    cost: int
    currency: str  # "gold" | "elixir" | "darkelixir"
    reasoning: str


class PlannerOutput(BaseModel):
    decisions: list[UpgradeDecision]
    updated_priorities: list[str]
    notes: str
