from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    project_root: Path
    openai_api_key: str | None
    openai_text_model: str
    openai_image_model: str
    tripo_api_key: str | None
    tripo_model_version: str
    tripo_texture_quality: str
    tripo_timeout_s: int
    blender_bin: str
    asset_factory_seed: int
    max_retries: int
    quality_preset: str
    character_hero_tris: int
    character_lod1_tris: int
    character_tex_size: int


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got: {raw!r}") from exc


def load_settings(project_root: Path | None = None) -> Settings:
    root = project_root or Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env", override=False)

    return Settings(
        project_root=root,
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        openai_text_model=os.getenv("OPENAI_TEXT_MODEL", "gpt-5-mini"),
        openai_image_model=os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1.5"),
        tripo_api_key=os.getenv("TRIPO_API_KEY"),
        tripo_model_version=os.getenv("TRIPO_MODEL_VERSION", "v2.5-20250123"),
        tripo_texture_quality=os.getenv("TRIPO_TEXTURE_QUALITY", "detailed"),
        tripo_timeout_s=_int_env("TRIPO_TIMEOUT_S", 900),
        blender_bin=os.getenv("BLENDER_BIN", "blender"),
        asset_factory_seed=_int_env("ASSET_FACTORY_SEED", 1337),
        max_retries=_int_env("ASSET_FACTORY_MAX_RETRIES", 2),
        quality_preset=os.getenv("ASSET_FACTORY_QUALITY_PRESET", "csgo"),
        character_hero_tris=_int_env("CHARACTER_HERO_TRIS", 25000),
        character_lod1_tris=_int_env("CHARACTER_LOD1_TRIS", 12000),
        character_tex_size=_int_env("CHARACTER_TEX_SIZE", 1024),
    )
