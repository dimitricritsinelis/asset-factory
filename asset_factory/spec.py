from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
import yaml


class StyleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    genre: str = Field(min_length=1)
    keywords: list[str] = Field(default_factory=list)


class TextureSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generate_basecolor: bool
    basecolor_size: Literal[256, 512, 1024]


class ColorSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    primary: str = Field(min_length=1)
    secondary: str = Field(min_length=1)


class TripoGeneratorSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["multiview", "image", "text"] = "multiview"
    rig: bool = False
    pbr: bool = True
    texture: bool = True
    animations: list[str] = Field(default_factory=list)
    animation_presets: dict[str, str] = Field(default_factory=dict)
    animation_source: Literal["curated", "tripo", "curated_then_tripo"] = "tripo"
    curated_animation_dir: str | None = None
    curated_clip_files: dict[str, str] = Field(default_factory=dict)
    retarget_map_path: str | None = None
    in_place: bool = True


class GeneratorSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["procedural", "tripo"] | None = None
    character_model_source: Literal["full_body", "base_human_hybrid"] = "full_body"
    tripo: TripoGeneratorSpec = Field(default_factory=TripoGeneratorSpec)


class WeaponAttachSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    socket_bone_name: str = "weapon_socket_r"
    bone_semantic: Literal["right_hand"] = "right_hand"
    offset_m: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0], min_length=3, max_length=3)
    rotation_deg: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0], min_length=3, max_length=3)
    scale: float = 1.0
    grip_right_offset_m: list[float] | None = Field(default=None, min_length=3, max_length=3)
    grip_left_offset_m: list[float] | None = Field(default=None, min_length=3, max_length=3)
    sight_offset_m: list[float] | None = Field(default=None, min_length=3, max_length=3)
    use_weapon_driven_ik: bool = False
    require_weapon_markers: bool = False
    marker_grip_right_name: str = "WPN_GRIP_R"
    marker_grip_left_name: str = "WPN_GRIP_L"
    marker_sight_name: str = "WPN_SIGHT"
    marker_muzzle_name: str = "WPN_MUZZLE"
    ads_eye_offset_m: list[float] | None = Field(default=None, min_length=3, max_length=3)
    ads_enabled: bool = False
    ads_clip_names: list[str] = Field(default_factory=list)
    ads_aim_distance_m: float = 12.0
    ads_head_bone: str | None = None
    ads_spine_bones: list[str] = Field(default_factory=list)
    qc_left_hand_grip_error_cm_max: float = Field(default=3.0, gt=0.0)
    qc_right_hand_grip_error_cm_max: float = Field(default=3.0, gt=0.0)
    qc_ads_eye_to_sight_m_max: float = Field(default=0.20, gt=0.0)
    qc_ads_eye_weapon_alignment_deg_max: float = Field(default=15.0, gt=0.0)
    qc_ads_sight_alignment_deg_max: float = Field(default=15.0, gt=0.0)
    qc_ads_wrist_delta_deg_max: float = Field(default=90.0, gt=0.0)


class PrimaryWeaponSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    spec: str | None = None
    legacy_id: str | None = Field(default=None, alias="id")
    legacy_class: str | None = Field(default=None, alias="class")
    embed_in_character_glb: bool = True
    attach: WeaponAttachSpec = Field(default_factory=WeaponAttachSpec)
    muzzle_socket_name: str = "muzzle"
    generate_if_missing: bool = True
    runtime_output_path: str | None = None
    legacy_output_path: str | None = Field(default=None, alias="output_path")
    tri_budget: int | None = None
    texture_size: int | None = None

    @model_validator(mode="after")
    def _resolve_spec_name(self) -> "PrimaryWeaponSpec":
        if self.spec is None and self.legacy_id:
            self.spec = self.legacy_id
        if not self.spec:
            raise ValueError("loadout.primary_weapon.spec is required (or provide legacy 'id')")
        if self.runtime_output_path is None and self.legacy_output_path:
            self.runtime_output_path = self.legacy_output_path
        return self


class LoadoutSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary_weapon: PrimaryWeaponSpec | None = None


class AssetSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1)
    kind: Literal["prop", "environment", "character"]
    description: str = Field(min_length=1)
    style: StyleSpec
    scale_meters: float = Field(gt=0)
    tri_budget: int = Field(gt=0)
    quality_preset: Literal["blockout", "csgo"] | None = None
    texture: TextureSpec
    colors: ColorSpec
    seed: int | None = None
    generator: GeneratorSpec | None = None
    character_qc_enforced: bool = False
    runtime_output_path: str | None = None
    loadout: LoadoutSpec | None = None

    def resolved_provider(self) -> Literal["procedural", "tripo"]:
        if self.generator and self.generator.provider:
            return self.generator.provider
        return "tripo" if self.kind == "character" else "procedural"

    def resolved_tripo(self) -> TripoGeneratorSpec:
        if self.generator:
            return self.generator.tripo
        default_mode = "multiview" if self.kind == "character" else "text"
        return TripoGeneratorSpec(mode=default_mode)

    def resolved_character_model_source(self) -> Literal["full_body", "base_human_hybrid"]:
        if self.generator:
            return self.generator.character_model_source
        return "full_body"

    def primary_weapon(self) -> PrimaryWeaponSpec | None:
        if not self.loadout:
            return None
        return self.loadout.primary_weapon


def resolve_spec_path(spec_path_or_name: str, project_root: Path) -> Path:
    explicit = Path(spec_path_or_name)
    if explicit.exists():
        return explicit

    spec_dirs = [
        project_root / "assets_pipeline" / "inputs",
        project_root / "assets_src" / "specs",
    ]
    candidate = spec_dirs[0] / (
        explicit.name if explicit.suffix in {".yaml", ".yml"} else f"{spec_path_or_name}.yaml"
    )
    for specs_dir in spec_dirs:
        current = specs_dir / (
            explicit.name if explicit.suffix in {".yaml", ".yml"} else f"{spec_path_or_name}.yaml"
        )
        if current.exists():
            return current

    raise FileNotFoundError(
        f"Spec not found: {spec_path_or_name!r}. Expected path or {candidate} "
        "(or legacy assets_src/specs)."
    )


def load_spec(path: Path) -> AssetSpec:
    if not path.exists():
        raise FileNotFoundError(f"Spec path does not exist: {path}")

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Spec file must contain a YAML mapping: {path}")

    return AssetSpec.model_validate(data)


def write_spec(path: Path, spec: AssetSpec) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    dumped = yaml.safe_dump(
        spec.model_dump(mode="python"),
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=False,
    )
    path.write_text(dumped, encoding="utf-8")
