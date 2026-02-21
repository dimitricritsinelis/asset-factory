#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _require(path: Path, label: str) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Missing {label}: {path}")


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    blender_bin = Path(os.environ.get("BLENDER_BIN", "/Applications/Blender.app/Contents/MacOS/Blender"))
    runner = root / "asset_factory" / "blender_runner.py"
    input_model = root / "assets_pipeline" / "tripo_raw" / "enemy_raider_01" / "model.glb"
    weapon_model = root / "assets" / "models" / "production" / "ak47.glb"
    retarget_map = root / "assets_pipeline" / "animation_library" / "enemy_raider_01" / "retarget_map.json"
    clip_dir = root / "assets_pipeline" / "animation_library" / "enemy_raider_01" / "clips"

    _require(blender_bin, "Blender binary")
    _require(runner, "Blender runner")
    _require(input_model, "input model")
    _require(weapon_model, "weapon model")
    _require(retarget_map, "retarget map")
    for clip in [
        "idle_rifle_in_place.glb",
        "walk_rifle_in_place.glb",
        "run_rifle_in_place.glb",
        "strafe_left_rifle_in_place.glb",
    ]:
        _require(clip_dir / clip, f"animation clip {clip}")

    out_glb = root / "assets" / "models" / "tests" / "enemy_raider_01_cleanup_test.glb"
    out_lod1 = root / "assets" / "models" / "tests" / "enemy_raider_01_cleanup_test_lod1.glb"
    out_blend = root / "assets_pipeline" / "blender" / "enemy_raider_01_cleanup_test.blend"
    out_thumb = root / "assets" / "thumbnails" / "tests" / "enemy_raider_01_cleanup_test.png"
    out_report = root / "assets" / "reports" / "tests" / "enemy_raider_01_cleanup_test.json"
    out_log = root / "assets_pipeline" / "logs" / "enemy_raider_01_cleanup_test.log"

    for p in [out_glb, out_lod1, out_blend, out_thumb, out_report, out_log]:
        p.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        str(blender_bin),
        "-b",
        "-P",
        str(runner),
        "--",
        "--input_model",
        str(input_model),
        "--mode",
        "character",
        "--target_height_m",
        "1.78",
        "--target_tris",
        "25000",
        "--target_tris_lod1",
        "12500",
        "--out",
        str(out_glb),
        "--out_lod1",
        str(out_lod1),
        "--blend",
        str(out_blend),
        "--thumb",
        str(out_thumb),
        "--report",
        str(out_report),
        "--log",
        str(out_log),
        "--primary_color",
        "#C2A97A",
        "--secondary_color",
        "#3F4A38",
        "--canonical_pose",
        "T",
        "--character",
        "--in_place",
        "--character_qc_enforced",
        "--anim_clip",
        f"idle_rifle_in_place={clip_dir / 'idle_rifle_in_place.glb'}",
        "--anim_clip",
        f"walk_rifle_in_place={clip_dir / 'walk_rifle_in_place.glb'}",
        "--anim_clip",
        f"run_rifle_in_place={clip_dir / 'run_rifle_in_place.glb'}",
        "--anim_clip",
        f"strafe_left_rifle_in_place={clip_dir / 'strafe_left_rifle_in_place.glb'}",
        "--requested_clip",
        "idle_rifle_in_place",
        "--requested_clip",
        "walk_rifle_in_place",
        "--requested_clip",
        "run_rifle_in_place",
        "--requested_clip",
        "strafe_left_rifle_in_place",
        "--retarget_map",
        str(retarget_map),
        "--weapon_model",
        str(weapon_model),
        "--weapon_socket_bone_name",
        "weapon_socket_r",
        "--weapon_bone_semantic",
        "right_hand",
        "--weapon_muzzle_socket_name",
        "muzzle",
        "--weapon_offset_m",
        "0,0,0",
        "--weapon_rotation_deg",
        "0,0,0",
        "--weapon_scale",
        "1.0",
    ]
    proc = subprocess.run(cmd, cwd=root, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"blender_runner failed with rc={proc.returncode}")

    report = json.loads(out_report.read_text(encoding="utf-8"))
    if report.get("status") != "ok":
        raise AssertionError(f"Expected status=ok, got {report.get('status')}")
    floating_after = int(report.get("floating_components_after", -1))
    if floating_after != 0:
        raise AssertionError(f"floating_components_after expected 0, got {floating_after}")
    floating_before = int(report.get("floating_components_before", -1))
    if floating_before < floating_after:
        raise AssertionError(
            f"floating_components_before ({floating_before}) must be >= floating_components_after ({floating_after})"
        )
    if floating_before > 0 and int(report.get("removed_loose_parts_by_distance", 0)) <= 0:
        raise AssertionError("Expected distance-based cleanup to remove at least one floating component")
    exported_names = [str(n) for n in report.get("exported_object_names", [])]
    if any(name in {"grip_r", "grip_l"} for name in exported_names):
        raise AssertionError("Internal grip helpers must not be exported")

    print("raider floating cleanup regression test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
