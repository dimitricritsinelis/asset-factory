from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class OpenAITripoPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    version: str = Field(min_length=1)
    asset_name: str = Field(min_length=1)
    reference_sheet_prompt: str = Field(min_length=1)
    tripo_prompt: str = Field(min_length=1)
    notes: list[str] = Field(default_factory=list)
