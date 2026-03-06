from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import shutil
from typing import Iterable

from .artifacts import (
    ArtifactPaths,
    compute_artifact_paths,
    ensure_artifact_dirs,
)
from .config import Settings
from .json_io import write_json
from .openai_io import generate_plan, generate_reference_sheet, split_reference_sheet
from .plan_schema import OpenAITripoPlan
from .prompts import make_plan_prompt, plan_system_prompt
from .reporting import set_report_status
from .spec import AssetSpec, canonical_smoke_spec_path, load_spec, resolve_spec_path, spec_sha256
from .tripo_io import generate_model_from_images

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class BuildOptions:
    force: bool = False
    fixture_replay: bool = False


def _timestamp_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _face_limit(target_tris: int) -> int:
    return max(4000, min(200000, int(target_tris)))


def _copy_if_exists(src: Path, dest: Path) -> None:
    if not src.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)


def _move_if_exists(src: Path, dest: Path) -> None:
    if not src.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.resolve() == dest.resolve():
        return
    if dest.exists():
        dest.unlink()
    src.replace(dest)


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _write_plan_log(plan: OpenAITripoPlan, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan.model_dump(mode="json"), indent=2), encoding="utf-8")


def _build_report(
    *,
    spec: AssetSpec,
    paths: ArtifactPaths,
    spec_digest: str,
    source_mode: str,
    project_root: Path,
    reasons: list[str] | None = None,
) -> dict:
    raw_model = paths.tripo_pbr_glb if paths.tripo_pbr_glb.exists() else paths.tripo_model_glb
    report = {
        "asset_name": spec.name,
        "status": "ok" if not reasons else "degraded",
        "timestamp_utc": _timestamp_utc(),
        "source_mode": source_mode,
        "spec_path": _relative(paths.spec_path, project_root),
        "spec_sha256": spec_digest,
        "openai_plan_path": _relative(paths.openai_plan_log, project_root) if paths.openai_plan_log.exists() else "",
        "reference_sheet_path": _relative(paths.ref_sheet, project_root) if paths.ref_sheet.exists() else "",
        "reference_views": {
            "front": _relative(paths.ref_front, project_root) if paths.ref_front.exists() else "",
            "side": _relative(paths.ref_side, project_root) if paths.ref_side.exists() else "",
            "back": _relative(paths.ref_back, project_root) if paths.ref_back.exists() else "",
        },
        "tripo_prompt_path": _relative(paths.tripo_prompt_log, project_root) if paths.tripo_prompt_log.exists() else "",
        "tripo_task_path": _relative(paths.tripo_task_json, project_root) if paths.tripo_task_json.exists() else "",
        "tripo_raw_model_path": _relative(raw_model, project_root) if raw_model.exists() else "",
        "tripo_render_path": _relative(paths.tripo_render_png, project_root) if paths.tripo_render_png.exists() else "",
        "export_path": _relative(paths.glb_file, project_root) if paths.glb_file.exists() else "",
        "tri_budget": int(spec.tri_budget),
        "scale_meters": float(spec.scale_meters),
    }
    if reasons:
        report["reason"] = reasons
        report["reasons"] = reasons
    return report


def _write_report(
    *,
    spec: AssetSpec,
    paths: ArtifactPaths,
    spec_digest: str,
    source_mode: str,
    project_root: Path,
    reasons: list[str] | None = None,
) -> None:
    report = _build_report(
        spec=spec,
        paths=paths,
        spec_digest=spec_digest,
        source_mode=source_mode,
        project_root=project_root,
        reasons=reasons,
    )
    write_json(paths.report_json, report)
    set_report_status(paths.report_json, "ok" if not reasons else "degraded", reasons or [])


def _fixture_dir(project_root: Path) -> Path:
    return project_root / "tests" / "fixtures"


def _same_path(left: Path, right: Path) -> bool:
    return left.resolve() == right.resolve()


def _canonical_smoke_spec_display(project_root: Path) -> str:
    return _relative(canonical_smoke_spec_path(project_root), project_root)


def _artifact_paths(
    *,
    project_root: Path,
    spec: AssetSpec,
    output_tier: str,
    spec_path: Path,
) -> ArtifactPaths:
    return compute_artifact_paths(
        project_root,
        spec.name,
        spec.asset_key,
        output_tier,
        spec_path=spec_path,
    )


def _stage_fixture(paths: ArtifactPaths, fixture_dir: Path) -> Path:
    required = {
        "plan": fixture_dir / "openai_plan.json",
        "sheet": fixture_dir / "sheet.png",
        "front": fixture_dir / "front.png",
        "side": fixture_dir / "side.png",
        "back": fixture_dir / "back.png",
        "task": fixture_dir / "task.json",
        "model": fixture_dir / "model.glb",
        "pbr": fixture_dir / "pbr.glb",
    }
    missing = [label for label, path in required.items() if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Fixture is incomplete at {fixture_dir}: missing {', '.join(missing)}")
    _copy_if_exists(required["plan"], paths.openai_plan_log)
    _copy_if_exists(required["sheet"], paths.ref_sheet)
    _copy_if_exists(required["front"], paths.ref_front)
    _copy_if_exists(required["side"], paths.ref_side)
    _copy_if_exists(required["back"], paths.ref_back)
    _copy_if_exists(required["task"], paths.tripo_task_json)
    _copy_if_exists(required["model"], paths.tripo_model_glb)
    _copy_if_exists(required["pbr"], paths.tripo_pbr_glb)
    _copy_if_exists(fixture_dir / "rendered.png", paths.tripo_render_png)
    _copy_if_exists(fixture_dir / "tripo_prompt.txt", paths.tripo_prompt_log)
    source_model = paths.tripo_pbr_glb if paths.tripo_pbr_glb.exists() else paths.tripo_model_glb
    _copy_if_exists(source_model, paths.glb_file)
    return source_model


def _normalize_tripo_outputs(paths: ArtifactPaths) -> None:
    generic_task = paths.tripo_raw_dir / "task.json"
    generic_model = paths.tripo_raw_dir / "model.glb"
    generic_pbr = paths.tripo_raw_dir / "pbr.glb"
    generic_render = paths.tripo_raw_dir / "rendered.png"
    _move_if_exists(generic_task, paths.tripo_task_json)
    _move_if_exists(generic_model, paths.tripo_model_glb)
    _move_if_exists(generic_pbr, paths.tripo_pbr_glb)
    _move_if_exists(generic_render, paths.tripo_render_png)


def _build_online(spec: AssetSpec, paths: ArtifactPaths, settings: Settings) -> Path:
    plan_prompt = make_plan_prompt(spec)
    paths.plan_prompt_log.write_text(plan_prompt + "\n", encoding="utf-8")
    _log.info(f"[build:{spec.name}] generating OpenAI plan")
    plan = generate_plan(system_prompt=plan_system_prompt(), user_prompt=plan_prompt, settings=settings)
    if plan.asset_name != spec.name:
        raise RuntimeError(
            f"OpenAI plan asset_name mismatch: expected {spec.name!r}, got {plan.asset_name!r}"
        )
    _write_plan_log(plan, paths.openai_plan_log)

    _log.info(f"[build:{spec.name}] generating reference sheet")
    generate_reference_sheet(plan.reference_sheet_prompt, paths.ref_sheet, settings)
    split_reference_sheet(paths.ref_sheet, paths.ref_front, paths.ref_side, paths.ref_back)

    paths.tripo_prompt_log.write_text(plan.tripo_prompt + "\n", encoding="utf-8")
    _log.info(f"[build:{spec.name}] calling Tripo")
    result = generate_model_from_images(
        images=[str(paths.ref_front), str(paths.ref_side), str(paths.ref_back)],
        prompt=plan.tripo_prompt,
        out_dir=paths.tripo_raw_dir,
        settings=settings,
        face_limit=_face_limit(spec.tri_budget),
        model_version=settings.tripo_model_version,
        texture_quality=settings.tripo_texture_quality,
    )
    _normalize_tripo_outputs(paths)
    source_model = result.pbr_model_path or result.model_path
    if paths.tripo_pbr_glb.exists():
        source_model = paths.tripo_pbr_glb
    elif paths.tripo_model_glb.exists():
        source_model = paths.tripo_model_glb
    if source_model is None or not source_model.exists():
        raise RuntimeError("Tripo returned no model output")
    _copy_if_exists(source_model, paths.glb_file)
    return source_model


def build_asset(spec_path_value: str | None, options: BuildOptions, settings: Settings) -> ArtifactPaths:
    spec_path = resolve_spec_path(spec_path_value, settings.project_root)
    spec = load_spec(spec_path)
    spec_digest = spec_sha256(spec_path)
    output_tier = "tests" if options.fixture_replay else "production"
    paths = _artifact_paths(
        project_root=settings.project_root,
        spec=spec,
        output_tier=output_tier,
        spec_path=spec_path,
    )
    ensure_artifact_dirs(paths)
    try:
        if options.fixture_replay:
            if not _same_path(spec_path, canonical_smoke_spec_path(settings.project_root)):
                raise RuntimeError(
                    "Fixture replay only supports the canonical smoke spec "
                    f"{_canonical_smoke_spec_display(settings.project_root)}."
                )
            fixture_dir = _fixture_dir(settings.project_root)
            _log.info(f"[build:{spec.name}] staging fixture replay from {fixture_dir}")
            _stage_fixture(paths, fixture_dir)
            _write_report(
                spec=spec,
                paths=paths,
                spec_digest=spec_digest,
                source_mode="fixture_replay",
                project_root=settings.project_root,
            )
        else:
            _build_online(spec, paths, settings)
            _write_report(
                spec=spec,
                paths=paths,
                spec_digest=spec_digest,
                source_mode="online",
                project_root=settings.project_root,
            )
        return paths
    except Exception as exc:  # noqa: BLE001
        reasons = [str(exc)]
        _write_report(
            spec=spec,
            paths=paths,
            spec_digest=spec_digest,
            source_mode="fixture_replay" if options.fixture_replay else "online",
            project_root=settings.project_root,
            reasons=reasons,
        )
        raise


def iter_artifact_paths(paths: ArtifactPaths) -> Iterable[Path]:
    ordered = [
        paths.ref_sheet,
        paths.ref_front,
        paths.ref_side,
        paths.ref_back,
        paths.openai_plan_log,
        paths.tripo_task_json,
        paths.tripo_model_glb,
        paths.tripo_pbr_glb,
        paths.tripo_render_png,
        paths.glb_file,
        paths.report_json,
        paths.plan_prompt_log,
        paths.tripo_prompt_log,
    ]
    for path in ordered:
        if path.exists():
            yield path
