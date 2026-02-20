from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import subprocess
from typing import Iterable, Literal

from .config import Settings
from .prompts import (
    make_character_sheet_prompt,
    make_image_prompt,
    make_new_spec_prompt,
    make_recipe_prompt,
    make_recipe_repair_prompt,
    make_tripo_prompt,
    make_weapon_sheet_prompt,
    recipe_system_prompt,
    spec_system_prompt,
)
from .recipe_schema import Recipe
from .spec import AssetSpec, PrimaryWeaponSpec, load_spec, resolve_spec_path, write_spec


@dataclass(frozen=True)
class BuildOptions:
    force: bool = False
    skip_images: bool = False
    skip_openai: bool = False
    provider_override: Literal["procedural", "tripo"] | None = None
    rig_override: bool | None = None
    dry_run: bool = False
    strict: bool = False


@dataclass(frozen=True)
class ArtifactPaths:
    ref_image: Path
    ref_sheet: Path
    ref_front: Path
    ref_side: Path
    ref_back: Path
    ref_top: Path
    recipe_json: Path
    tripo_raw_dir: Path
    tripo_task_json: Path
    tripo_model_glb: Path
    tripo_pbr_glb: Path
    tripo_render_png: Path
    tripo_animations_dir: Path
    blend_file: Path
    glb_file: Path
    lod1_glb_file: Path
    thumbnail_png: Path
    report_json: Path
    build_log: Path
    image_prompt_log: Path
    recipe_prompt_log: Path
    tripo_prompt_log: Path


@dataclass(frozen=True)
class WeaponBuildOutcome:
    glb_path: Path
    runtime_path: Path
    report_path: Path
    built: bool
    degraded: bool
    reasons: list[str]


def _artifact_paths(project_root: Path, name: str) -> ArtifactPaths:
    tripo_raw = project_root / "assets_src" / "tripo_raw" / name
    return ArtifactPaths(
        ref_image=project_root / "assets_src" / "refs" / f"{name}.png",
        ref_sheet=project_root / "assets_src" / "refs" / f"{name}_sheet.png",
        ref_front=project_root / "assets_src" / "refs" / f"{name}_front.png",
        ref_side=project_root / "assets_src" / "refs" / f"{name}_side.png",
        ref_back=project_root / "assets_src" / "refs" / f"{name}_back.png",
        ref_top=project_root / "assets_src" / "refs" / f"{name}_top.png",
        recipe_json=project_root / "assets_src" / "recipes" / f"{name}.json",
        tripo_raw_dir=tripo_raw,
        tripo_task_json=tripo_raw / "task.json",
        tripo_model_glb=tripo_raw / "model.glb",
        tripo_pbr_glb=tripo_raw / "pbr.glb",
        tripo_render_png=tripo_raw / "rendered.png",
        tripo_animations_dir=tripo_raw / "animations",
        blend_file=project_root / "assets_src" / "blender" / f"{name}.blend",
        glb_file=project_root / "assets" / "models" / f"{name}.glb",
        lod1_glb_file=project_root / "assets" / "models" / f"{name}_lod1.glb",
        thumbnail_png=project_root / "assets" / "thumbnails" / f"{name}.png",
        report_json=project_root / "assets" / "reports" / f"{name}.json",
        build_log=project_root / "assets_src" / "logs" / f"{name}.log",
        image_prompt_log=project_root / "assets_src" / "logs" / f"{name}_image_prompt.txt",
        recipe_prompt_log=project_root / "assets_src" / "logs" / f"{name}_recipe_prompt.txt",
        tripo_prompt_log=project_root / "assets_src" / "logs" / f"{name}_tripo_prompt.txt",
    )


def _ensure_parent_dirs(paths: ArtifactPaths) -> None:
    for path in [
        paths.ref_image,
        paths.ref_sheet,
        paths.ref_front,
        paths.ref_side,
        paths.ref_back,
        paths.ref_top,
        paths.recipe_json,
        paths.blend_file,
        paths.glb_file,
        paths.lod1_glb_file,
        paths.thumbnail_png,
        paths.report_json,
        paths.build_log,
        paths.image_prompt_log,
        paths.recipe_prompt_log,
        paths.tripo_prompt_log,
        paths.tripo_task_json,
        paths.tripo_model_glb,
        paths.tripo_pbr_glb,
        paths.tripo_render_png,
        paths.tripo_animations_dir / ".keep",
    ]:
        path.parent.mkdir(parents=True, exist_ok=True)


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def _color_to_rgba(color: str, fallback: tuple[float, float, float, float]) -> list[float]:
    c = color.strip().lower()
    named: dict[str, tuple[float, float, float, float]] = {
        "dusty sand": (0.76, 0.66, 0.48, 1.0),
        "dark olive": (0.25, 0.29, 0.22, 1.0),
        "sand": (0.78, 0.71, 0.55, 1.0),
        "olive": (0.33, 0.39, 0.27, 1.0),
        "gray": (0.6, 0.6, 0.6, 1.0),
    }
    if c in named:
        return list(named[c])
    if c.startswith("#"):
        hx = c[1:]
        if len(hx) == 3:
            hx = "".join(ch * 2 for ch in hx)
        if len(hx) == 6:
            try:
                r = int(hx[0:2], 16) / 255.0
                g = int(hx[2:4], 16) / 255.0
                b = int(hx[4:6], 16) / 255.0
                return [_clamp01(r), _clamp01(g), _clamp01(b), 1.0]
            except ValueError:
                pass
    return list(fallback)


def _placeholder_recipe(spec: AssetSpec) -> Recipe:
    return Recipe.model_validate(
        {
            "version": "1",
            "name": spec.name,
            "units_scale_m": spec.scale_meters,
            "objects": [
                {
                    "name": f"{spec.name}_body",
                    "primitive": "cube",
                    "location": [0.0, 0.0, 0.5 * spec.scale_meters],
                    "rotation_deg": [0.0, 0.0, 0.0],
                    "scale": [
                        0.5 * spec.scale_meters,
                        0.5 * spec.scale_meters,
                        0.5 * spec.scale_meters,
                    ],
                    "ops": [{"type": "bevel", "width": 0.03 * spec.scale_meters, "segments": 2}],
                    "material": "mat_main",
                }
            ],
            "materials": [
                {
                    "name": "mat_main",
                    "basecolor": {"mode": "solid", "color_rgba": [0.7, 0.7, 0.7, 1.0]},
                }
            ],
            "export": {
                "format": "glb",
                "apply_transforms": True,
                "triangulate": True,
                "uv_unwrap": "smart",
                "target_tris": spec.tri_budget,
            },
            "thumbnail": {
                "size": 256,
                "camera_angle_deg": [55.0, 0.0, 35.0],
                "light_strength": 4.0,
            },
        }
    )


def _fallback_mannequin_recipe(spec: AssetSpec) -> Recipe:
    primary = _color_to_rgba(spec.colors.primary, (0.7, 0.7, 0.7, 1.0))
    secondary = _color_to_rgba(spec.colors.secondary, (0.35, 0.4, 0.35, 1.0))
    h = spec.scale_meters
    return Recipe.model_validate(
        {
            "version": "1",
            "name": spec.name,
            "units_scale_m": h,
            "objects": [
                {
                    "name": "head",
                    "primitive": "cube",
                    "location": [0.0, 0.0, 0.92 * h],
                    "rotation_deg": [0.0, 0.0, 0.0],
                    "scale": [0.10 * h, 0.10 * h, 0.10 * h],
                    "ops": [{"type": "bevel", "width": 0.01 * h, "segments": 2}],
                    "material": "mat_primary",
                },
                {
                    "name": "torso",
                    "primitive": "cube",
                    "location": [0.0, 0.0, 0.66 * h],
                    "rotation_deg": [0.0, 0.0, 0.0],
                    "scale": [0.14 * h, 0.09 * h, 0.18 * h],
                    "ops": [{"type": "bevel", "width": 0.012 * h, "segments": 2}],
                    "material": "mat_primary",
                },
                {
                    "name": "hips",
                    "primitive": "cube",
                    "location": [0.0, 0.0, 0.45 * h],
                    "rotation_deg": [0.0, 0.0, 0.0],
                    "scale": [0.13 * h, 0.08 * h, 0.10 * h],
                    "ops": [{"type": "bevel", "width": 0.01 * h, "segments": 2}],
                    "material": "mat_primary",
                },
                {
                    "name": "arm_left",
                    "primitive": "cylinder",
                    "location": [0.18 * h, 0.0, 0.66 * h],
                    "rotation_deg": [0.0, 0.0, 0.0],
                    "scale": [0.03 * h, 0.03 * h, 0.16 * h],
                    "ops": [{"type": "shade_smooth"}],
                    "material": "mat_secondary",
                },
                {
                    "name": "arm_right",
                    "primitive": "cylinder",
                    "location": [-0.18 * h, 0.0, 0.66 * h],
                    "rotation_deg": [0.0, 0.0, 0.0],
                    "scale": [0.03 * h, 0.03 * h, 0.16 * h],
                    "ops": [{"type": "shade_smooth"}],
                    "material": "mat_secondary",
                },
                {
                    "name": "leg_left",
                    "primitive": "cylinder",
                    "location": [0.07 * h, 0.0, 0.22 * h],
                    "rotation_deg": [0.0, 0.0, 0.0],
                    "scale": [0.035 * h, 0.035 * h, 0.22 * h],
                    "ops": [{"type": "shade_smooth"}],
                    "material": "mat_secondary",
                },
                {
                    "name": "leg_right",
                    "primitive": "cylinder",
                    "location": [-0.07 * h, 0.0, 0.22 * h],
                    "rotation_deg": [0.0, 0.0, 0.0],
                    "scale": [0.035 * h, 0.035 * h, 0.22 * h],
                    "ops": [{"type": "shade_smooth"}],
                    "material": "mat_secondary",
                },
            ],
            "materials": [
                {"name": "mat_primary", "basecolor": {"mode": "solid", "color_rgba": primary}},
                {"name": "mat_secondary", "basecolor": {"mode": "solid", "color_rgba": secondary}},
            ],
            "export": {
                "format": "glb",
                "apply_transforms": True,
                "triangulate": True,
                "uv_unwrap": "smart",
                "target_tris": max(1500, min(spec.tri_budget, 8000)),
            },
            "thumbnail": {
                "size": 256,
                "camera_angle_deg": [55.0, 0.0, 35.0],
                "light_strength": 4.0,
            },
        }
    )


def _save_recipe(path: Path, recipe: Recipe | dict) -> None:
    payload = recipe.model_dump(mode="json") if isinstance(recipe, Recipe) else recipe
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _load_recipe(path: Path) -> Recipe:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return Recipe.model_validate(payload)


def _tail_for_prompt(log_path: Path, max_chars: int = 12000) -> str:
    if not log_path.exists():
        return ""
    data = log_path.read_text(encoding="utf-8", errors="replace")
    return data[-max_chars:]


def _needs_blender_run(paths: ArtifactPaths, force: bool, require_lod1: bool = False) -> bool:
    if force:
        return True
    needed = [paths.glb_file, paths.blend_file, paths.thumbnail_png, paths.report_json]
    if require_lod1:
        needed.append(paths.lod1_glb_file)
    return not all(p.exists() for p in needed)


def _run_blender_recipe(paths: ArtifactPaths, settings: Settings) -> int:
    cmd = [
        settings.blender_bin,
        "-b",
        "-P",
        "asset_factory/blender_runner.py",
        "--",
        "--recipe",
        str(paths.recipe_json),
        "--out",
        str(paths.glb_file),
        "--blend",
        str(paths.blend_file),
        "--thumb",
        str(paths.thumbnail_png),
        "--report",
        str(paths.report_json),
        "--log",
        str(paths.build_log),
    ]

    with paths.build_log.open("w", encoding="utf-8") as handle:
        try:
            process = subprocess.run(
                cmd,
                cwd=settings.project_root,
                stdout=handle,
                stderr=subprocess.STDOUT,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "Blender executable not found. Install Blender and ensure it is on PATH as 'blender', "
                "or set BLENDER_BIN (example macOS: /Applications/Blender.app/Contents/MacOS/Blender)."
            ) from exc
    return process.returncode


def _run_blender_normalize(
    *,
    input_model: Path,
    target_height_m: float,
    target_length_m: float | None,
    target_tris_lod0: int,
    target_tris_lod1: int,
    paths: ArtifactPaths,
    settings: Settings,
    primary_color: str,
    secondary_color: str,
    mode: Literal["generic", "character", "weapon"],
    animation_models: list[Path] | None = None,
    weapon_model: Path | None = None,
    weapon_socket_bone_name: str = "weapon_socket_r",
    weapon_bone_semantic: str = "right_hand",
    weapon_muzzle_socket_name: str = "muzzle",
    weapon_offset_m: tuple[float, float, float] = (0.0, 0.0, 0.0),
    weapon_rotation_deg: tuple[float, float, float] = (0.0, 0.0, 0.0),
    weapon_scale: float = 1.0,
) -> int:
    cmd = [
        settings.blender_bin,
        "-b",
        "-P",
        "asset_factory/blender_runner.py",
        "--",
        "--input_model",
        str(input_model),
        "--mode",
        mode,
        "--target_height_m",
        str(target_height_m),
        "--target_tris",
        str(target_tris_lod0),
        "--target_tris_lod1",
        str(target_tris_lod1),
        "--out_lod1",
        str(paths.lod1_glb_file),
        "--primary_color",
        primary_color,
        "--secondary_color",
        secondary_color,
        "--out",
        str(paths.glb_file),
        "--blend",
        str(paths.blend_file),
        "--thumb",
        str(paths.thumbnail_png),
        "--report",
        str(paths.report_json),
        "--log",
        str(paths.build_log),
    ]
    if target_length_m is not None:
        cmd.extend(["--target_length_m", str(target_length_m)])
    if mode == "character":
        cmd.append("--character")
    for anim_model in animation_models or []:
        cmd.extend(["--anim_model", str(anim_model)])
    if weapon_model is not None:
        cmd.extend(
            [
                "--weapon_model",
                str(weapon_model),
                "--weapon_socket_bone_name",
                weapon_socket_bone_name,
                "--weapon_bone_semantic",
                weapon_bone_semantic,
                "--weapon_muzzle_socket_name",
                weapon_muzzle_socket_name,
                "--weapon_offset_m",
                f"{weapon_offset_m[0]},{weapon_offset_m[1]},{weapon_offset_m[2]}",
                "--weapon_rotation_deg",
                f"{weapon_rotation_deg[0]},{weapon_rotation_deg[1]},{weapon_rotation_deg[2]}",
                "--weapon_scale",
                str(weapon_scale),
            ]
        )

    with paths.build_log.open("w", encoding="utf-8") as handle:
        try:
            process = subprocess.run(
                cmd,
                cwd=settings.project_root,
                stdout=handle,
                stderr=subprocess.STDOUT,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "Blender executable not found. Install Blender and ensure it is on PATH as 'blender', "
                "or set BLENDER_BIN (example macOS: /Applications/Blender.app/Contents/MacOS/Blender)."
            ) from exc
    return process.returncode


def _resolve_provider(spec: AssetSpec, options: BuildOptions) -> Literal["procedural", "tripo"]:
    if options.provider_override is not None:
        return options.provider_override
    return spec.resolved_provider()


def _resolve_rig(spec: AssetSpec, options: BuildOptions) -> bool:
    if options.rig_override is not None:
        return options.rig_override
    return spec.resolved_tripo().rig


def _effective_quality_preset(spec: AssetSpec, settings: Settings) -> Literal["blockout", "csgo"]:
    if spec.quality_preset:
        return spec.quality_preset
    env_value = settings.quality_preset if settings.quality_preset in {"blockout", "csgo"} else None
    if spec.kind == "character":
        return "csgo" if env_value is None else env_value
    if env_value is None or env_value == "csgo":
        return "blockout"
    return env_value


def _character_targets(spec: AssetSpec, settings: Settings) -> tuple[int, int]:
    hero = max(int(spec.tri_budget), int(settings.character_hero_tris))
    if spec.tri_budget < hero:
        lod1 = int(spec.tri_budget)
    else:
        lod1 = min(int(spec.tri_budget), int(settings.character_lod1_tris))
    lod1 = max(1000, min(lod1, hero))
    return hero, lod1


def _tripo_face_limit(target_tris: int) -> int:
    return max(4000, min(200000, int(target_tris)))


def _default_character_clips() -> list[str]:
    return ["idle", "walk", "run"]


def _canonical_requested_clip(raw: str) -> str:
    clip = raw.strip().lower().replace(" ", "_")
    if clip.startswith("preset:"):
        clip = clip.split("preset:", 1)[1].replace(":", "_")
    if clip.endswith("_in_place"):
        clip = clip[: -len("_in_place")]
    aliases = {
        "walk_cycle": "walk",
        "run_cycle": "run",
        "idle_loop": "idle",
        "death": "fall",
        "strafe_left": "walk",
        "strafe_right": "walk",
    }
    return aliases.get(clip, clip)


def _requested_animation_clips(spec: AssetSpec) -> list[str]:
    if spec.kind != "character":
        return []
    clips = [_canonical_requested_clip(c) for c in spec.resolved_tripo().animations if c and c.strip()]
    merged = _default_character_clips() + clips
    out: list[str] = []
    seen: set[str] = set()
    for clip in merged:
        key = clip.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(clip)
    return out


def _load_report(report_path: Path) -> dict:
    if not report_path.exists():
        return {}
    try:
        return json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _write_report(report_path: Path, payload: dict) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _update_report_fields(report_path: Path, fields: dict) -> None:
    report = _load_report(report_path)
    report.update(fields)
    _write_report(report_path, report)


def _mark_report_status(report_path: Path, status: Literal["ok", "degraded"], reasons: list[str]) -> None:
    report = _load_report(report_path)
    report["status"] = status
    if reasons:
        report["reason"] = reasons
        report["reasons"] = reasons
    else:
        report.pop("reason", None)
        report.pop("reasons", None)
    _write_report(report_path, report)


def _normalize_qc_issues(report: dict, spec: AssetSpec, lod0_target: int, lod1_target: int) -> list[str]:
    issues: list[str] = []

    tris_lod0 = report.get("tris_lod0")
    if isinstance(tris_lod0, (int, float)) and tris_lod0 > lod0_target * 1.1:
        issues.append(f"tris_lod0 {tris_lod0} exceeds allowed max {lod0_target * 1.1:.0f}")

    tris_lod1 = report.get("tris_lod1")
    if isinstance(tris_lod1, (int, float)) and tris_lod1 > lod1_target * 1.1:
        issues.append(f"tris_lod1 {tris_lod1} exceeds allowed max {lod1_target * 1.1:.0f}")

    height_m = report.get("height_m")
    if isinstance(height_m, (int, float)):
        rel = abs(float(height_m) - spec.scale_meters) / max(spec.scale_meters, 1e-6)
        if rel > 0.12:
            issues.append(f"height {height_m:.3f}m differs from target {spec.scale_meters:.3f}m")

    bbox_min = report.get("bbox_min")
    if isinstance(bbox_min, list) and len(bbox_min) >= 3 and isinstance(bbox_min[2], (int, float)):
        if bbox_min[2] < -0.03:
            issues.append(f"model origin not grounded: min_z={bbox_min[2]:.4f}")

    objects_after = report.get("objects_after", report.get("object_count"))
    if isinstance(objects_after, int) and objects_after > 3:
        issues.append(f"normalized object_count too high: {objects_after}")

    return issues


def _weapon_qc_issues(report: dict, spec: AssetSpec, target_tris: int) -> list[str]:
    issues: list[str] = []
    tris = report.get("tris_lod0", report.get("triangle_count"))
    if isinstance(tris, (int, float)) and tris > target_tris * 1.1:
        issues.append(f"weapon tris {tris} exceeds allowed max {target_tris * 1.1:.0f}")
    length_m = report.get("length_m")
    if isinstance(length_m, (int, float)):
        rel = abs(float(length_m) - spec.scale_meters) / max(spec.scale_meters, 1e-6)
        if rel > 0.15:
            issues.append(f"weapon length {length_m:.3f}m differs from target {spec.scale_meters:.3f}m")
    return issues


def _copy_lod0_to_lod1(paths: ArtifactPaths) -> None:
    if paths.glb_file.exists() and not paths.lod1_glb_file.exists():
        shutil.copyfile(paths.glb_file, paths.lod1_glb_file)


def _looks_like_weapon(spec: AssetSpec) -> bool:
    text = " ".join(
        [
            spec.name.lower(),
            spec.description.lower(),
            spec.style.genre.lower(),
            " ".join(k.lower() for k in spec.style.keywords),
        ]
    )
    weapon_terms = ("weapon", "rifle", "pistol", "smg", "shotgun", "sniper", "ak")
    return spec.kind == "prop" and any(term in text for term in weapon_terms)


def _runtime_output_path(raw_path: str, project_root: Path) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return project_root / path


def _default_weapon_runtime_output(name: str, project_root: Path) -> Path:
    return project_root / "apps" / "client" / "public" / "assets" / "models" / "weapons" / name / f"{name}.glb"


def _resolve_runtime_output(spec: AssetSpec, project_root: Path, *, is_weapon: bool) -> Path | None:
    if spec.runtime_output_path:
        return _runtime_output_path(spec.runtime_output_path, project_root)
    if is_weapon:
        return _default_weapon_runtime_output(spec.name, project_root)
    return None


def _copy_runtime_glb(src: Path, runtime_path: Path | None) -> None:
    if runtime_path is None:
        return
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, runtime_path)


def _sync_runtime_output_for_spec(spec: AssetSpec, paths: ArtifactPaths, settings: Settings) -> Path | None:
    runtime_path = _resolve_runtime_output(spec, settings.project_root, is_weapon=_looks_like_weapon(spec))
    if runtime_path is not None and paths.glb_file.exists():
        _copy_runtime_glb(paths.glb_file, runtime_path)
        _update_report_fields(paths.report_json, {"runtime_output_path": str(runtime_path)})
    return runtime_path


def _fallback_weapon_recipe(spec: AssetSpec) -> Recipe:
    body = _color_to_rgba(spec.colors.primary, (0.18, 0.18, 0.18, 1.0))
    accent = _color_to_rgba(spec.colors.secondary, (0.36, 0.22, 0.12, 1.0))
    length = max(0.65, min(1.2, spec.scale_meters))
    return Recipe.model_validate(
        {
            "version": "1",
            "name": spec.name,
            "units_scale_m": length,
            "objects": [
                {
                    "name": "receiver",
                    "primitive": "cube",
                    "location": [0.0, 0.0, 0.08],
                    "rotation_deg": [0.0, 0.0, 0.0],
                    "scale": [0.16, 0.05, 0.05],
                    "ops": [{"type": "bevel", "width": 0.007, "segments": 2}],
                    "material": "mat_body",
                },
                {
                    "name": "barrel",
                    "primitive": "cylinder",
                    "location": [0.0, 0.22, 0.08],
                    "rotation_deg": [90.0, 0.0, 0.0],
                    "scale": [0.012, 0.012, 0.20],
                    "ops": [{"type": "shade_smooth"}],
                    "material": "mat_body",
                },
                {
                    "name": "stock",
                    "primitive": "cube",
                    "location": [0.0, -0.20, 0.08],
                    "rotation_deg": [0.0, 0.0, 0.0],
                    "scale": [0.11, 0.04, 0.04],
                    "ops": [{"type": "bevel", "width": 0.006, "segments": 2}],
                    "material": "mat_body",
                },
                {
                    "name": "mag",
                    "primitive": "cube",
                    "location": [0.0, -0.02, -0.02],
                    "rotation_deg": [0.0, 8.0, 0.0],
                    "scale": [0.04, 0.025, 0.08],
                    "ops": [{"type": "bevel", "width": 0.005, "segments": 2}],
                    "material": "mat_accent",
                },
                {
                    "name": "grip",
                    "primitive": "cube",
                    "location": [0.0, -0.07, 0.00],
                    "rotation_deg": [0.0, 12.0, 0.0],
                    "scale": [0.03, 0.025, 0.07],
                    "ops": [{"type": "bevel", "width": 0.005, "segments": 2}],
                    "material": "mat_accent",
                },
            ],
            "materials": [
                {"name": "mat_body", "basecolor": {"mode": "solid", "color_rgba": body}},
                {"name": "mat_accent", "basecolor": {"mode": "solid", "color_rgba": accent}},
            ],
            "export": {
                "format": "glb",
                "apply_transforms": True,
                "triangulate": True,
                "uv_unwrap": "smart",
                "target_tris": max(1200, min(spec.tri_budget, 12000)),
            },
            "thumbnail": {
                "size": 256,
                "camera_angle_deg": [55.0, 0.0, 35.0],
                "light_strength": 4.0,
            },
        }
    )


def _ensure_weapon_sheet_refs(
    *,
    spec: AssetSpec,
    options: BuildOptions,
    settings: Settings,
    paths: ArtifactPaths,
) -> list[Path]:
    from .openai_io import generate_weapon_sheet, split_weapon_sheet

    prompt = make_weapon_sheet_prompt(spec)
    paths.image_prompt_log.write_text(prompt + "\n", encoding="utf-8")
    need_sheet = options.force or not paths.ref_sheet.exists()
    if need_sheet:
        if options.skip_images:
            raise RuntimeError(
                "Weapon provider=tripo mode=multiview requires refs when --skip-images is set. "
                "Provide existing refs: <name>_front.png, <name>_side.png, <name>_top.png"
            )
        generate_weapon_sheet(prompt=prompt, out_path=paths.ref_sheet, settings=settings)

    need_split = options.force or not paths.ref_front.exists() or not paths.ref_side.exists() or not paths.ref_top.exists()
    if need_split:
        split_weapon_sheet(paths.ref_sheet, paths.ref_front, paths.ref_side, paths.ref_top)
    return [paths.ref_front, paths.ref_side, paths.ref_top]


def _loadout_weapon_runtime_path(loadout: PrimaryWeaponSpec, project_root: Path) -> Path | None:
    if loadout.runtime_output_path:
        return _runtime_output_path(loadout.runtime_output_path, project_root)
    return None


def _ensure_character_sheet_refs(
    *,
    spec: AssetSpec,
    options: BuildOptions,
    settings: Settings,
    paths: ArtifactPaths,
) -> list[Path]:
    from .openai_io import generate_character_sheet, split_character_sheet

    sheet_prompt = make_character_sheet_prompt(spec)
    paths.image_prompt_log.write_text(sheet_prompt + "\n", encoding="utf-8")

    need_sheet = options.force or not paths.ref_sheet.exists()
    if need_sheet:
        if options.skip_images:
            raise RuntimeError(
                "Character provider=tripo mode=multiview requires split refs when --skip-images is set. "
                "Provide existing refs: <name>_front.png, <name>_side.png, <name>_back.png"
            )
        generate_character_sheet(prompt=sheet_prompt, out_path=paths.ref_sheet, settings=settings)

    need_split = (
        options.force
        or not paths.ref_front.exists()
        or not paths.ref_side.exists()
        or not paths.ref_back.exists()
    )
    if need_split:
        split_character_sheet(paths.ref_sheet, paths.ref_front, paths.ref_side, paths.ref_back)

    return [paths.ref_front, paths.ref_side, paths.ref_back]


def _ensure_image_ref(
    *,
    spec: AssetSpec,
    options: BuildOptions,
    settings: Settings,
    paths: ArtifactPaths,
) -> list[Path]:
    from .openai_io import generate_concept_image

    if options.skip_images:
        if paths.ref_front.exists():
            return [paths.ref_front]
        if paths.ref_image.exists():
            return [paths.ref_image]
        raise RuntimeError("Image mode requested but no local ref image exists while --skip-images is set")

    image_prompt = make_image_prompt(spec)
    paths.image_prompt_log.write_text(image_prompt + "\n", encoding="utf-8")
    if options.force or not paths.ref_image.exists():
        generate_concept_image(image_prompt, paths.ref_image, settings)
    return [paths.ref_image]


def ensure_weapon_built(
    *,
    weapon_spec_name_or_path: str,
    options: BuildOptions,
    settings: Settings,
    runtime_override: Path | None = None,
) -> WeaponBuildOutcome:
    from .tripo_io import generate_model_from_images, generate_model_from_text

    weapon_spec_path = resolve_spec_path(weapon_spec_name_or_path, settings.project_root)
    weapon_spec = load_spec(weapon_spec_path)
    if weapon_spec.kind != "prop":
        raise RuntimeError(f"Weapon spec must be kind=prop, got {weapon_spec.kind} ({weapon_spec_path})")

    paths = _artifact_paths(settings.project_root, weapon_spec.name)
    _ensure_parent_dirs(paths)

    runtime_path = runtime_override or _resolve_runtime_output(weapon_spec, settings.project_root, is_weapon=True)
    if runtime_path is None:
        runtime_path = _default_weapon_runtime_output(weapon_spec.name, settings.project_root)

    if not options.force and runtime_path.exists():
        return WeaponBuildOutcome(
            glb_path=runtime_path,
            runtime_path=runtime_path,
            report_path=paths.report_json,
            built=False,
            degraded=False,
            reasons=[],
        )

    if not options.force and paths.glb_file.exists():
        _copy_runtime_glb(paths.glb_file, runtime_path)
        return WeaponBuildOutcome(
            glb_path=runtime_path,
            runtime_path=runtime_path,
            report_path=paths.report_json,
            built=False,
            degraded=False,
            reasons=[],
        )

    reasons: list[str] = []
    provider = options.provider_override or weapon_spec.resolved_provider()
    if options.skip_openai:
        provider = "procedural"
        reasons.append("provider tripo disabled by --skip-openai")

    if provider == "tripo":
        try:
            tripo_cfg = weapon_spec.resolved_tripo()
            tripo_prompt = make_tripo_prompt(weapon_spec)
            paths.tripo_prompt_log.write_text(tripo_prompt + "\n", encoding="utf-8")
            face_limit = _tripo_face_limit(weapon_spec.tri_budget)

            if tripo_cfg.mode == "multiview":
                refs = _ensure_weapon_sheet_refs(spec=weapon_spec, options=options, settings=settings, paths=paths)
                result = generate_model_from_images(
                    images=[str(p) for p in refs],
                    prompt=tripo_prompt,
                    out_dir=paths.tripo_raw_dir,
                    settings=settings,
                    face_limit=face_limit,
                    model_version=settings.tripo_model_version,
                    texture_quality=settings.tripo_texture_quality,
                    pbr=tripo_cfg.pbr,
                    texture=tripo_cfg.texture,
                    rig=False,
                )
            elif tripo_cfg.mode == "image":
                refs = _ensure_image_ref(spec=weapon_spec, options=options, settings=settings, paths=paths)
                result = generate_model_from_images(
                    images=[str(refs[0])],
                    prompt=tripo_prompt,
                    out_dir=paths.tripo_raw_dir,
                    settings=settings,
                    face_limit=face_limit,
                    model_version=settings.tripo_model_version,
                    texture_quality=settings.tripo_texture_quality,
                    pbr=tripo_cfg.pbr,
                    texture=tripo_cfg.texture,
                    rig=False,
                )
            else:
                result = generate_model_from_text(
                    prompt=tripo_prompt,
                    out_dir=paths.tripo_raw_dir,
                    settings=settings,
                    face_limit=face_limit,
                    model_version=settings.tripo_model_version,
                    texture_quality=settings.tripo_texture_quality,
                    pbr=tripo_cfg.pbr,
                    texture=tripo_cfg.texture,
                    rig=False,
                )

            source_model = result.pbr_model_path or result.model_path
            if source_model is None or not source_model.exists():
                raise RuntimeError("Tripo returned no model output for weapon")

            rc = _run_blender_normalize(
                input_model=source_model,
                target_height_m=max(0.5, weapon_spec.scale_meters),
                target_length_m=weapon_spec.scale_meters,
                target_tris_lod0=weapon_spec.tri_budget,
                target_tris_lod1=weapon_spec.tri_budget,
                paths=paths,
                settings=settings,
                primary_color=weapon_spec.colors.primary,
                secondary_color=weapon_spec.colors.secondary,
                mode="weapon",
                animation_models=None,
            )
            if rc != 0:
                raise RuntimeError(f"Blender weapon normalize failed with rc={rc}. See {paths.build_log}")

            _copy_runtime_glb(paths.glb_file, runtime_path)
            report = _load_report(paths.report_json)
            degraded = str(report.get("status", "ok")).lower() == "degraded"
            return WeaponBuildOutcome(
                glb_path=runtime_path,
                runtime_path=runtime_path,
                report_path=paths.report_json,
                built=True,
                degraded=degraded,
                reasons=report.get("reasons", []) if isinstance(report.get("reasons"), list) else [],
            )
        except Exception as exc:  # noqa: BLE001
            reasons.append(f"weapon tripo pipeline failed: {exc}")

    fallback_options = BuildOptions(
        force=True,
        skip_images=True,
        skip_openai=True,
        provider_override="procedural",
        rig_override=False,
        dry_run=False,
        strict=False,
    )
    fallback_recipe = _fallback_weapon_recipe(weapon_spec)
    _build_with_procedural(
        spec=weapon_spec,
        options=fallback_options,
        settings=settings,
        paths=paths,
        forced_recipe=fallback_recipe,
    )
    _copy_runtime_glb(paths.glb_file, runtime_path)
    reasons.append("weapon fallback used procedural placeholder")
    _mark_report_status(paths.report_json, "degraded", reasons)

    return WeaponBuildOutcome(
        glb_path=runtime_path,
        runtime_path=runtime_path,
        report_path=paths.report_json,
        built=True,
        degraded=True,
        reasons=reasons,
    )


def _build_with_procedural(
    *,
    spec: AssetSpec,
    options: BuildOptions,
    settings: Settings,
    paths: ArtifactPaths,
    forced_recipe: Recipe | None = None,
) -> ArtifactPaths:
    from .openai_io import generate_concept_image, generate_recipe

    image_prompt = make_image_prompt(spec)
    paths.image_prompt_log.write_text(image_prompt + "\n", encoding="utf-8")

    if options.force or not paths.ref_image.exists():
        if options.skip_images or options.skip_openai:
            print(f"[build:{spec.name}] skipping concept image")
        else:
            print(f"[build:{spec.name}] generating concept image")
            generate_concept_image(image_prompt, paths.ref_image, settings)
    else:
        print(f"[build:{spec.name}] reusing concept image")

    if options.force or not paths.recipe_json.exists() or forced_recipe is not None:
        if forced_recipe is not None:
            recipe = forced_recipe
            _save_recipe(paths.recipe_json, recipe)
            paths.recipe_prompt_log.write_text("forced fallback recipe used\n", encoding="utf-8")
            print(f"[build:{spec.name}] wrote forced fallback recipe")
        elif options.skip_openai:
            recipe = _fallback_mannequin_recipe(spec) if spec.kind == "character" else _placeholder_recipe(spec)
            _save_recipe(paths.recipe_json, recipe)
            paths.recipe_prompt_log.write_text(
                "offline placeholder recipe used (--skip-openai)\n", encoding="utf-8"
            )
            print(f"[build:{spec.name}] wrote offline placeholder recipe")
        else:
            has_ref = paths.ref_image.exists() and not options.skip_images
            user_prompt = make_recipe_prompt(spec, has_ref_image=has_ref)
            paths.recipe_prompt_log.write_text(user_prompt + "\n", encoding="utf-8")
            recipe = generate_recipe(
                system_prompt=recipe_system_prompt(),
                user_prompt=user_prompt,
                settings=settings,
                image_path=paths.ref_image if has_ref else None,
            )
            _save_recipe(paths.recipe_json, recipe)
            print(f"[build:{spec.name}] generated recipe with OpenAI")
    else:
        print(f"[build:{spec.name}] reusing recipe")

    if not _needs_blender_run(paths, options.force, require_lod1=False):
        print(f"[build:{spec.name}] outputs already exist; skipping Blender")
        _sync_runtime_output_for_spec(spec, paths, settings)
        return paths

    print(f"[build:{spec.name}] running Blender (procedural)")
    rc = _run_blender_recipe(paths, settings)
    if rc == 0:
        if spec.kind == "character":
            _copy_lod0_to_lod1(paths)
        _sync_runtime_output_for_spec(spec, paths, settings)
        return paths

    if options.skip_openai or forced_recipe is not None:
        raise RuntimeError(f"Blender failed for {spec.name} in offline/fallback mode. See log: {paths.build_log}")

    recipe = _load_recipe(paths.recipe_json)
    for attempt in range(1, settings.max_retries + 1):
        error_tail = _tail_for_prompt(paths.build_log)
        repair_prompt = make_recipe_repair_prompt(
            spec=spec,
            current_recipe_json=json.dumps(recipe.model_dump(mode="json"), indent=2),
            blender_error_log=error_tail,
        )
        repair_log = paths.build_log.parent / f"{spec.name}_repair_prompt_{attempt}.txt"
        repair_log.write_text(repair_prompt + "\n", encoding="utf-8")

        recipe = generate_recipe(
            system_prompt=recipe_system_prompt(),
            user_prompt=repair_prompt,
            settings=settings,
            image_path=paths.ref_image if paths.ref_image.exists() else None,
        )
        _save_recipe(paths.recipe_json, recipe)

        rc = _run_blender_recipe(paths, settings)
        if rc == 0:
            if spec.kind == "character":
                _copy_lod0_to_lod1(paths)
            print(f"[build:{spec.name}] repaired recipe on attempt {attempt}")
            _sync_runtime_output_for_spec(spec, paths, settings)
            return paths

    raise RuntimeError(f"Blender failed after {settings.max_retries} retries for {spec.name}. See {paths.build_log}")


def _run_final_fallback(
    *,
    spec: AssetSpec,
    options: BuildOptions,
    settings: Settings,
    paths: ArtifactPaths,
    reasons: list[str],
) -> ArtifactPaths:
    print(f"[build:{spec.name}] running final fallback mannequin")
    fallback_recipe = _fallback_mannequin_recipe(spec)
    fallback_options = BuildOptions(
        force=True,
        skip_images=True,
        skip_openai=True,
        provider_override="procedural",
        rig_override=False,
        dry_run=False,
        strict=False,
    )
    try:
        out_paths = _build_with_procedural(
            spec=spec,
            options=fallback_options,
            settings=settings,
            paths=paths,
            forced_recipe=fallback_recipe,
        )
        _sync_runtime_output_for_spec(spec, paths, settings)
        reasons.append("final fallback used procedural mannequin")
        _mark_report_status(paths.report_json, "degraded", reasons)
        if options.strict:
            raise RuntimeError("Build completed in degraded mode (strict enabled)")
        return out_paths
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"procedural fallback failed: {exc}")

        # Last-resort salvage: copy any existing Tripo model directly as final GLB.
        salvage = paths.tripo_pbr_glb if paths.tripo_pbr_glb.exists() else paths.tripo_model_glb
        if salvage.exists():
            shutil.copyfile(salvage, paths.glb_file)
            if not paths.lod1_glb_file.exists():
                shutil.copyfile(paths.glb_file, paths.lod1_glb_file)
            _sync_runtime_output_for_spec(spec, paths, settings)
            report = {
                "status": "degraded",
                "reason": reasons,
                "export_path": str(paths.glb_file),
                "lod1_export_path": str(paths.lod1_glb_file),
                "salvaged_from": str(salvage),
            }
            _write_report(paths.report_json, report)
            if options.strict:
                raise RuntimeError("Build completed in degraded mode (strict enabled)")
            return paths
        raise


def _build_with_tripo(
    *,
    spec: AssetSpec,
    options: BuildOptions,
    settings: Settings,
    paths: ArtifactPaths,
) -> ArtifactPaths:
    from .tripo_io import generate_animation_clips, generate_model_from_images, generate_model_from_text

    degraded_reasons: list[str] = []
    weapon_cfg: PrimaryWeaponSpec | None = spec.primary_weapon() if spec.kind == "character" else None

    if options.skip_openai:
        print(
            f"[build:{spec.name}] --skip-openai active; provider=tripo disabled for this run, using procedural offline path"
        )
        out_paths = _build_with_procedural(spec=spec, options=options, settings=settings, paths=paths)
        if weapon_cfg is not None and weapon_cfg.embed_in_character_glb:
            try:
                runtime_override = _loadout_weapon_runtime_path(weapon_cfg, settings.project_root)
                weapon_outcome = ensure_weapon_built(
                    weapon_spec_name_or_path=weapon_cfg.spec or "",
                    options=BuildOptions(
                        force=options.force,
                        skip_images=True,
                        skip_openai=True,
                        provider_override="procedural",
                        rig_override=False,
                        dry_run=False,
                        strict=False,
                    ),
                    settings=settings,
                    runtime_override=runtime_override,
                )
                _update_report_fields(
                    paths.report_json,
                    {
                        "weapon_built": bool(weapon_outcome.glb_path.exists()),
                        "weapon_path": str(weapon_outcome.glb_path),
                        "weapon_embedded": False,
                        "muzzle_socket_name": weapon_cfg.muzzle_socket_name,
                    },
                )
                degraded_reasons.extend(weapon_outcome.reasons)
                degraded_reasons.append("weapon not embedded in offline procedural character fallback")
            except Exception as exc:  # noqa: BLE001
                degraded_reasons.append(f"weapon offline fallback failed: {exc}")
        degraded_reasons.append("provider tripo disabled by --skip-openai")
        _mark_report_status(paths.report_json, "degraded", degraded_reasons)
        if options.strict:
            raise RuntimeError("Build completed in degraded mode (strict enabled)")
        return out_paths

    tripo_cfg = spec.resolved_tripo()
    preferred_mode = tripo_cfg.mode
    requested_rig = _resolve_rig(spec, options)

    quality_preset = _effective_quality_preset(spec, settings)
    if spec.kind == "character" and quality_preset == "csgo":
        lod0_target, lod1_target = _character_targets(spec, settings)
    else:
        lod0_target, lod1_target = spec.tri_budget, min(spec.tri_budget, settings.character_lod1_tris)

    face_limit = _tripo_face_limit(lod0_target)

    tripo_prompt = make_tripo_prompt(spec)
    paths.tripo_prompt_log.write_text(tripo_prompt + "\n", encoding="utf-8")

    image_multiview: list[Path] = []
    image_single: list[Path] = []
    is_weapon = _looks_like_weapon(spec)
    if preferred_mode in {"multiview", "image"}:
        try:
            if preferred_mode == "multiview":
                if is_weapon:
                    image_multiview = _ensure_weapon_sheet_refs(
                        spec=spec,
                        options=options,
                        settings=settings,
                        paths=paths,
                    )
                else:
                    image_multiview = _ensure_character_sheet_refs(
                        spec=spec,
                        options=options,
                        settings=settings,
                        paths=paths,
                    )
                image_single = [image_multiview[0]] if image_multiview else []
            else:
                image_single = _ensure_image_ref(
                    spec=spec,
                    options=options,
                    settings=settings,
                    paths=paths,
                )
        except Exception as exc:  # noqa: BLE001
            degraded_reasons.append(f"reference image preparation failed: {exc}")

    build_json = {
        "provider": "tripo",
        "mode": preferred_mode,
        "quality_preset": quality_preset,
        "tri_budget": spec.tri_budget,
        "hero_tris": lod0_target,
        "lod1_tris": lod1_target,
        "character_tex_size": settings.character_tex_size,
        "face_limit": face_limit,
        "model_version": settings.tripo_model_version,
        "texture_quality": settings.tripo_texture_quality,
        "rig": requested_rig,
        "animations": _requested_animation_clips(spec),
        "in_place": tripo_cfg.in_place,
    }
    _save_recipe(paths.recipe_json, build_json)

    source_model: Path | None = None
    source_task_payload: dict = {}
    animation_models: list[Path] = []
    weapon_outcome: WeaponBuildOutcome | None = None
    weapon_model_path: Path | None = None

    if not options.force and (paths.tripo_pbr_glb.exists() or paths.tripo_model_glb.exists()):
        print(f"[build:{spec.name}] reusing Tripo raw model")
        source_model = paths.tripo_pbr_glb if paths.tripo_pbr_glb.exists() else paths.tripo_model_glb
        source_task_payload = _load_json(paths.tripo_task_json)
    else:
        attempts: list[tuple[str, str, bool, list[Path]]] = []

        if image_multiview:
            attempts.append(("A", "multiview", requested_rig, image_multiview))
            attempts.append(("B", "multiview", False, image_multiview))
        if image_single:
            attempts.append(("C", "image", False, image_single))
        attempts.append(("D", "text", False, []))

        for label, mode, use_rig, images in attempts:
            try:
                print(f"[build:{spec.name}] Tripo attempt {label}: mode={mode}, rig={use_rig}")
                if mode in {"multiview", "image"} and images:
                    result = generate_model_from_images(
                        images=[str(p) for p in images],
                        prompt=tripo_prompt,
                        out_dir=paths.tripo_raw_dir,
                        settings=settings,
                        face_limit=face_limit,
                        model_version=settings.tripo_model_version,
                        texture_quality=settings.tripo_texture_quality,
                        pbr=tripo_cfg.pbr,
                        texture=tripo_cfg.texture,
                        rig=use_rig,
                    )
                else:
                    result = generate_model_from_text(
                        prompt=tripo_prompt,
                        out_dir=paths.tripo_raw_dir,
                        settings=settings,
                        face_limit=face_limit,
                        model_version=settings.tripo_model_version,
                        texture_quality=settings.tripo_texture_quality,
                        pbr=tripo_cfg.pbr,
                        texture=tripo_cfg.texture,
                        rig=use_rig,
                    )

                source_model = result.pbr_model_path or result.model_path
                if source_model and source_model.exists():
                    source_task_payload = result.task_payload
                    if label != "A":
                        degraded_reasons.append(f"Tripo fallback used attempt {label} ({mode}, rig={use_rig})")
                    break
                degraded_reasons.append(f"attempt {label} produced no model output")
            except Exception as exc:  # noqa: BLE001
                degraded_reasons.append(f"Tripo attempt {label} failed: {exc}")

    if source_model is None or not source_model.exists():
        return _run_final_fallback(
            spec=spec,
            options=options,
            settings=settings,
            paths=paths,
            reasons=degraded_reasons,
        )

    requested_clips = _requested_animation_clips(spec)
    if requested_clips:
        if not source_task_payload:
            source_task_payload = _load_json(paths.tripo_task_json)
        try:
            clip_results = generate_animation_clips(
                source_task_payload=source_task_payload,
                clip_names=requested_clips,
                in_place=tripo_cfg.in_place,
                out_dir=paths.tripo_raw_dir,
                settings=settings,
            )
            ok_clips = [r for r in clip_results if r.path is not None and r.path.exists()]
            failed = [r for r in clip_results if r.path is None]
            animation_models = [r.path for r in ok_clips if r.path is not None]
            for bad in failed:
                degraded_reasons.append(f"animation clip '{bad.clip_name}' failed: {bad.error}")
            if not animation_models:
                degraded_reasons.append("all requested animation clips failed")
        except Exception as exc:  # noqa: BLE001
            degraded_reasons.append(f"animation stage failed: {exc}")

    if weapon_cfg is not None and weapon_cfg.embed_in_character_glb:
        try:
            runtime_override = _loadout_weapon_runtime_path(weapon_cfg, settings.project_root)
            if not weapon_cfg.generate_if_missing and runtime_override is not None and not runtime_override.exists():
                degraded_reasons.append(
                    f"weapon file missing and generate_if_missing=false: {runtime_override}"
                )
            else:
                weapon_outcome = ensure_weapon_built(
                    weapon_spec_name_or_path=weapon_cfg.spec or "",
                    options=BuildOptions(
                        force=options.force,
                        skip_images=options.skip_images,
                        skip_openai=options.skip_openai,
                        provider_override=None,
                        rig_override=False,
                        dry_run=False,
                        strict=False,
                    ),
                    settings=settings,
                    runtime_override=runtime_override,
                )
                weapon_model_path = weapon_outcome.glb_path
                if weapon_outcome.degraded:
                    degraded_reasons.extend(weapon_outcome.reasons or ["weapon build degraded"])
        except Exception as exc:  # noqa: BLE001
            degraded_reasons.append(f"weapon pipeline failed: {exc}")

    require_lod1 = spec.kind == "character"
    if not _needs_blender_run(paths, options.force, require_lod1=require_lod1):
        print(f"[build:{spec.name}] outputs already exist; skipping Blender normalize")
        _sync_runtime_output_for_spec(spec, paths, settings)
        if weapon_cfg is not None:
            _update_report_fields(
                paths.report_json,
                {
                    "weapon_built": bool(weapon_outcome and weapon_outcome.glb_path.exists()),
                    "weapon_path": str(weapon_outcome.glb_path) if weapon_outcome else "",
                    "weapon_embedded": bool(weapon_cfg.embed_in_character_glb),
                    "muzzle_socket_name": weapon_cfg.muzzle_socket_name,
                },
            )
        if degraded_reasons:
            _mark_report_status(paths.report_json, "degraded", degraded_reasons)
            if options.strict:
                raise RuntimeError("Build completed in degraded mode (strict enabled)")
        return paths

    normalize_mode: Literal["generic", "character", "weapon"]
    if _looks_like_weapon(spec):
        normalize_mode = "weapon"
    elif spec.kind == "character":
        normalize_mode = "character"
    else:
        normalize_mode = "generic"

    normalize_ok = False
    for attempt in range(1, settings.max_retries + 2):
        print(f"[build:{spec.name}] running Blender normalize (attempt {attempt})")
        weapon_offset = (0.0, 0.0, 0.0)
        weapon_rot = (0.0, 0.0, 0.0)
        weapon_scale = 1.0
        socket_bone = "weapon_socket_r"
        socket_semantic = "right_hand"
        muzzle_name = "muzzle"
        if weapon_cfg is not None:
            weapon_offset = tuple(float(v) for v in weapon_cfg.attach.offset_m)
            weapon_rot = tuple(float(v) for v in weapon_cfg.attach.rotation_deg)
            weapon_scale = float(weapon_cfg.attach.scale)
            socket_bone = weapon_cfg.attach.socket_bone_name
            socket_semantic = weapon_cfg.attach.bone_semantic
            muzzle_name = weapon_cfg.muzzle_socket_name

        rc = _run_blender_normalize(
            input_model=source_model,
            target_height_m=spec.scale_meters,
            target_length_m=spec.scale_meters if normalize_mode == "weapon" else None,
            target_tris_lod0=lod0_target,
            target_tris_lod1=lod1_target,
            paths=paths,
            settings=settings,
            primary_color=spec.colors.primary,
            secondary_color=spec.colors.secondary,
            mode=normalize_mode,
            animation_models=animation_models,
            weapon_model=weapon_model_path if weapon_cfg is not None and weapon_cfg.embed_in_character_glb else None,
            weapon_socket_bone_name=socket_bone,
            weapon_bone_semantic=socket_semantic,
            weapon_muzzle_socket_name=muzzle_name,
            weapon_offset_m=weapon_offset,
            weapon_rotation_deg=weapon_rot,
            weapon_scale=weapon_scale,
        )
        if rc != 0:
            degraded_reasons.append(f"Blender normalize attempt {attempt} failed (rc={rc})")
            continue

        report = _load_report(paths.report_json)
        report_status = str(report.get("status", "ok")).lower()
        report_reasons = report.get("reasons", report.get("reason", []))
        if report_status == "degraded":
            if isinstance(report_reasons, list):
                degraded_reasons.extend(str(r) for r in report_reasons)
            elif report_reasons:
                degraded_reasons.append(str(report_reasons))

        clip_count = report.get("clip_count")
        if requested_clips and isinstance(clip_count, int):
            if clip_count < len(requested_clips):
                degraded_reasons.append(
                    f"missing animation clips in export: got {clip_count}, expected {len(requested_clips)}"
                )
            if clip_count <= 0:
                degraded_reasons.append("export contains no animation clips")
        if normalize_mode == "character":
            qc_issues = _normalize_qc_issues(report, spec, lod0_target=lod0_target, lod1_target=lod1_target)
        elif normalize_mode == "weapon":
            qc_issues = _weapon_qc_issues(report, spec, target_tris=lod0_target)
        else:
            qc_issues = []
        if qc_issues:
            degraded_reasons.extend(qc_issues)
            qc_log = paths.build_log.parent / f"{spec.name}_normalize_qc_{attempt}.txt"
            qc_log.write_text("\n".join(qc_issues) + "\n", encoding="utf-8")
            # Keep result as usable; continue only if retries remain.
            if attempt <= settings.max_retries:
                continue

        normalize_ok = True
        break

    if not normalize_ok:
        return _run_final_fallback(
            spec=spec,
            options=options,
            settings=settings,
            paths=paths,
            reasons=degraded_reasons,
        )

    runtime_output = _resolve_runtime_output(spec, settings.project_root, is_weapon=_looks_like_weapon(spec))
    if runtime_output is not None and paths.glb_file.exists():
        _copy_runtime_glb(paths.glb_file, runtime_output)

    report_fields: dict = {}
    if runtime_output is not None:
        report_fields["runtime_output_path"] = str(runtime_output)
    if weapon_cfg is not None:
        report_fields.update(
            {
                "weapon_built": bool(weapon_model_path and weapon_model_path.exists()),
                "weapon_path": str(weapon_model_path) if weapon_model_path else "",
                "muzzle_socket_name": weapon_cfg.muzzle_socket_name,
            }
        )
    if normalize_mode == "weapon":
        report_fields.setdefault("weapon_built", True)
        report_fields.setdefault("weapon_path", str(runtime_output) if runtime_output else str(paths.glb_file))
        report_fields.setdefault("weapon_embedded", False)
        report_fields.setdefault("muzzle_socket_name", "muzzle")
    if report_fields:
        _update_report_fields(paths.report_json, report_fields)

    if degraded_reasons:
        _mark_report_status(paths.report_json, "degraded", degraded_reasons)
        if options.strict:
            raise RuntimeError("Build completed in degraded mode (strict enabled)")
    else:
        _mark_report_status(paths.report_json, "ok", [])

    if spec.kind == "character":
        _copy_lod0_to_lod1(paths)

    return paths


def build_asset(spec_path_or_name: str, options: BuildOptions, settings: Settings) -> ArtifactPaths:
    spec_path = resolve_spec_path(spec_path_or_name, settings.project_root)
    spec = load_spec(spec_path)
    paths = _artifact_paths(settings.project_root, spec.name)
    _ensure_parent_dirs(paths)

    provider = _resolve_provider(spec, options)

    if options.dry_run:
        quality_preset = _effective_quality_preset(spec, settings)
        lod0_target, lod1_target = _character_targets(spec, settings)
        requested_clips = _requested_animation_clips(spec)
        runtime_output = _resolve_runtime_output(spec, settings.project_root, is_weapon=_looks_like_weapon(spec))
        print(f"[dry-run] spec={spec_path}")
        print(f"[dry-run] provider={provider}")
        print(f"[dry-run] quality_preset={quality_preset}")
        print(f"[dry-run] output_glb={paths.glb_file}")
        print(f"[dry-run] output_glb_lod1={paths.lod1_glb_file}")
        print(f"[dry-run] report={paths.report_json}")
        if runtime_output is not None:
            print(f"[dry-run] runtime_output={runtime_output}")
        if spec.kind == "character":
            print(f"[dry-run] target_tris_lod0={lod0_target}")
            print(f"[dry-run] target_tris_lod1={lod1_target}")
            print(f"[dry-run] requested_clips={requested_clips}")
            weapon = spec.primary_weapon()
            if weapon is not None and weapon.embed_in_character_glb:
                print(f"[dry-run] loadout.primary_weapon.spec={weapon.spec}")
                print(f"[dry-run] loadout.primary_weapon.socket={weapon.attach.socket_bone_name}")
        if provider == "tripo":
            print(f"[dry-run] tripo_raw_dir={paths.tripo_raw_dir}")
        return paths

    if provider == "tripo":
        return _build_with_tripo(spec=spec, options=options, settings=settings, paths=paths)

    output_paths = _build_with_procedural(spec=spec, options=options, settings=settings, paths=paths)
    _mark_report_status(paths.report_json, "ok", [])
    return output_paths


def build_all(options: BuildOptions, settings: Settings) -> None:
    specs_dir = settings.project_root / "assets_src" / "specs"
    spec_paths = sorted(p for p in specs_dir.glob("*.yaml") if p.is_file())
    if not spec_paths:
        raise RuntimeError(f"No YAML specs found in {specs_dir}")

    failures: list[str] = []
    for spec_path in spec_paths:
        try:
            build_asset(str(spec_path), options, settings)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{spec_path.name}: {exc}")
            print(f"[build-all] FAILED {spec_path.name}: {exc}")

    if failures:
        raise RuntimeError("Build-all completed with failures:\n" + "\n".join(failures))


def create_new_spec(name: str, desc: str, settings: Settings) -> Path:
    from .openai_io import generate_spec

    if not name.replace("_", "").replace("-", "").isalnum():
        raise ValueError("Spec name must be alphanumeric with optional '-' or '_' characters")

    out_path = settings.project_root / "assets_src" / "specs" / f"{name}.yaml"
    if out_path.exists():
        raise FileExistsError(f"Spec already exists: {out_path}")

    generated = generate_spec(
        system_prompt=spec_system_prompt(),
        user_prompt=make_new_spec_prompt(name, desc),
        settings=settings,
    )

    generated = generated.model_copy(update={"name": name})
    write_spec(out_path, generated)
    return out_path


def iter_artifact_paths(paths: ArtifactPaths) -> Iterable[Path]:
    base = [
        paths.ref_image,
        paths.recipe_json,
        paths.blend_file,
        paths.glb_file,
        paths.thumbnail_png,
        paths.report_json,
        paths.build_log,
    ]
    optional = [
        paths.ref_sheet,
        paths.ref_front,
        paths.ref_side,
        paths.ref_back,
        paths.ref_top,
        paths.tripo_task_json,
        paths.tripo_model_glb,
        paths.tripo_pbr_glb,
        paths.tripo_render_png,
        paths.lod1_glb_file,
        paths.tripo_animations_dir,
    ]
    for p in optional:
        if p.exists():
            base.append(p)
    return base
