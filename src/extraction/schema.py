"""Structured intent schema per project_plan.md Phase 3: action / object / specification / quantity."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Intent(BaseModel):
    action: str = Field(description="Verb describing the required work, e.g. 'construct', 'provide'")
    object: str = Field(description="The building element or system acted on, e.g. 'staircase', 'external wall'")
    specification: dict[str, str] = Field(
        default_factory=dict,
        description="Key/value engineering parameters, e.g. {'width': '1.2m', 'material': 'RCC'}",
    )
    quantity: str | None = Field(default=None, description="Count or extent, e.g. '3 units', '450 sqm'")
    source_page: int | None = Field(default=None, description="Page number the intent was extracted from, if known")


class ExtractionResult(BaseModel):
    intents: list[Intent]
