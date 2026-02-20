from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class BevelOp(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["bevel"]
    width: float = Field(gt=0)
    segments: int = Field(ge=1, le=8)


class SubdivideOp(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["subdivide"]
    levels: int = Field(ge=1, le=6)


class DecimateOp(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["decimate"]
    ratio: float = Field(gt=0, le=1.0)


class ShadeSmoothOp(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["shade_smooth"]


class MirrorOp(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["mirror"]
    axis: Literal["X", "Y", "Z"]


class NoiseDisplaceOp(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["noise_displace"]
    strength: float = Field(ge=0)
    scale: float = Field(gt=0)


Operation = Annotated[
    Union[BevelOp, SubdivideOp, DecimateOp, ShadeSmoothOp, MirrorOp, NoiseDisplaceOp],
    Field(discriminator="type"),
]


class ObjectSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    primitive: Literal["cube", "cylinder", "uv_sphere", "ico_sphere", "plane"]
    location: tuple[float, float, float]
    rotation_deg: tuple[float, float, float]
    scale: tuple[float, float, float]
    ops: list[Operation] = Field(default_factory=list)
    material: str = Field(min_length=1)


class SolidBaseColor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["solid"]
    color_rgba: tuple[float, float, float, float]

    @model_validator(mode="before")
    @classmethod
    def _flatten_nested_solid(cls, value):
        if isinstance(value, dict) and value.get("mode") == "solid":
            nested = value.get("solid")
            if isinstance(nested, dict) and "color_rgba" in nested and "color_rgba" not in value:
                fixed = dict(value)
                fixed["color_rgba"] = nested["color_rgba"]
                fixed.pop("solid", None)
                return fixed
        return value

    @field_validator("color_rgba")
    @classmethod
    def _rgba_in_range(cls, value: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        if any(channel < 0 or channel > 1 for channel in value):
            raise ValueError("color_rgba channels must be in [0,1]")
        return value


class TextureBaseColor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["texture"]
    texture_path: str = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _flatten_nested_texture(cls, value):
        if isinstance(value, dict) and value.get("mode") == "texture":
            nested = value.get("texture")
            if isinstance(nested, dict) and "texture_path" in nested and "texture_path" not in value:
                fixed = dict(value)
                fixed["texture_path"] = nested["texture_path"]
                fixed.pop("texture", None)
                return fixed
        return value


BaseColorSpec = Annotated[Union[SolidBaseColor, TextureBaseColor], Field(discriminator="mode")]


class MaterialSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    basecolor: BaseColorSpec


class ExportSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["glb"] = "glb"
    apply_transforms: bool = True
    triangulate: bool = True
    uv_unwrap: Literal["smart", "none"] = "smart"
    target_tris: int = Field(gt=0)


class ThumbnailSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    size: Literal[256] = 256
    camera_angle_deg: tuple[float, float, float] = (55.0, 0.0, 35.0)
    light_strength: float = Field(gt=0, default=4.0)


class Recipe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["1"] = "1"
    name: str = Field(min_length=1)
    units_scale_m: float = Field(gt=0)
    objects: list[ObjectSpec] = Field(min_length=1)
    materials: list[MaterialSpec] = Field(min_length=1)
    export: ExportSpec
    thumbnail: ThumbnailSpec
