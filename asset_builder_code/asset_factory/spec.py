from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator
import yaml


_CANONICAL_SMOKE_SPEC = Path("asset_definitions") / "enemy_raider.yaml"
_CANONICAL_ASSET_NAME = "enemy_raider"


class StyleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    genre: str = Field(min_length=1)
    keywords: list[str] = Field(default_factory=list)


class ColorSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    primary: str = Field(min_length=1)
    secondary: str = Field(min_length=1)


class AssetSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    style: StyleSpec
    scale_meters: float = Field(gt=0)
    tri_budget: int = Field(gt=0)
    colors: ColorSpec
    seed: int | None = None

    @model_validator(mode="after")
    def _validate_name(self) -> "AssetSpec":
        if self.name != _CANONICAL_ASSET_NAME:
            raise ValueError(f"Only {_CANONICAL_ASSET_NAME!r} is supported by this repo.")
        return self

    @property
    def asset_key(self) -> str:
        return _CANONICAL_ASSET_NAME


def canonical_smoke_spec_path(project_root: Path) -> Path:
    return project_root / _CANONICAL_SMOKE_SPEC


def resolve_spec_path(spec_path_value: str | None, project_root: Path) -> Path:
    if spec_path_value is None or not str(spec_path_value).strip():
        raise ValueError("Spec path is required. Use asset_definitions/enemy_raider.yaml.")
    raw_path = Path(spec_path_value)
    resolved = raw_path if raw_path.is_absolute() else project_root / raw_path
    canonical = canonical_smoke_spec_path(project_root)
    if resolved != canonical:
        raise ValueError(f"Only {canonical.relative_to(project_root)} is supported.")
    if not resolved.exists():
        raise FileNotFoundError(f"Spec path does not exist: {resolved}")
    return resolved


def load_spec(path: Path) -> AssetSpec:
    if not path.exists():
        raise FileNotFoundError(f"Spec path does not exist: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Spec file must contain a YAML mapping: {path}")
    return AssetSpec.model_validate(data)


def spec_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_spec(path: Path, spec: AssetSpec) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    dumped = yaml.safe_dump(
        spec.model_dump(mode="python"),
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=False,
    )
    path.write_text(dumped, encoding="utf-8")
