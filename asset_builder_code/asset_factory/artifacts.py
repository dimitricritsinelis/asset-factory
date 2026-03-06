from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Literal

OutputTier = Literal["production", "tests"]


@dataclass(frozen=True)
class ArtifactPaths:
    asset_name: str
    asset_key: str
    output_tier: OutputTier
    spec_path: Path
    ref_sheet: Path
    ref_front: Path
    ref_side: Path
    ref_back: Path
    tripo_raw_dir: Path
    tripo_task_json: Path
    tripo_model_glb: Path
    tripo_pbr_glb: Path
    tripo_render_png: Path
    glb_file: Path
    report_json: Path
    openai_plan_log: Path
    plan_prompt_log: Path
    tripo_prompt_log: Path


def compute_artifact_paths(
    project_root: Path,
    asset_name: str,
    asset_key: str,
    output_tier: OutputTier,
    *,
    spec_path: Path,
) -> ArtifactPaths:
    output_dir = project_root / "generated_asset_files"
    refs_dir = output_dir / "openai_2d_reference_images" / asset_key
    logs_dir = output_dir / "openai_logs" / asset_key
    tripo_raw_dir = output_dir / "tripo_3d_raw_outputs" / asset_key
    models_dir = output_dir / "final_3d_models" / output_tier
    reports_dir = output_dir / "build_reports" / output_tier
    return ArtifactPaths(
        asset_name=asset_name,
        asset_key=asset_key,
        output_tier=output_tier,
        spec_path=spec_path,
        ref_sheet=refs_dir / f"{asset_key}_sheet.png",
        ref_front=refs_dir / f"{asset_key}_front.png",
        ref_side=refs_dir / f"{asset_key}_side.png",
        ref_back=refs_dir / f"{asset_key}_back.png",
        tripo_raw_dir=tripo_raw_dir,
        tripo_task_json=tripo_raw_dir / f"{asset_key}_task.json",
        tripo_model_glb=tripo_raw_dir / f"{asset_key}_model.glb",
        tripo_pbr_glb=tripo_raw_dir / f"{asset_key}_pbr.glb",
        tripo_render_png=tripo_raw_dir / f"{asset_key}_rendered.png",
        glb_file=models_dir / f"{asset_key}.glb",
        report_json=reports_dir / f"{asset_key}.json",
        openai_plan_log=logs_dir / f"{asset_key}_openai_plan.json",
        plan_prompt_log=logs_dir / f"{asset_key}_plan_prompt.txt",
        tripo_prompt_log=logs_dir / f"{asset_key}_tripo_prompt.txt",
    )


def _dir_seed_paths(paths: ArtifactPaths) -> Iterator[Path]:
    yield paths.ref_sheet
    yield paths.ref_front
    yield paths.ref_side
    yield paths.ref_back
    yield paths.tripo_task_json
    yield paths.tripo_model_glb
    yield paths.tripo_pbr_glb
    yield paths.tripo_render_png
    yield paths.glb_file
    yield paths.report_json
    yield paths.openai_plan_log
    yield paths.plan_prompt_log
    yield paths.tripo_prompt_log
    yield paths.tripo_raw_dir / ".keep"


def ensure_artifact_dirs(paths: ArtifactPaths) -> None:
    for seed in _dir_seed_paths(paths):
        seed.parent.mkdir(parents=True, exist_ok=True)
