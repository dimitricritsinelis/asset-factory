#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import struct
import subprocess
from pathlib import Path


RIFLE_LOCOMOTION_CLIPS = [
    "idle_rifle_in_place",
    "walk_rifle_in_place",
    "run_rifle_in_place",
    "strafe_left_rifle_in_place",
]


def _require(path: Path, label: str) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Missing {label}: {path}")


def _metric(metrics: dict, clip_name: str, key: str) -> float:
    clip_payload = metrics.get(clip_name)
    if not isinstance(clip_payload, dict):
        raise AssertionError(f"Missing movement metrics for clip: {clip_name}")
    value = clip_payload.get(key)
    if not isinstance(value, (int, float)):
        raise AssertionError(f"Missing numeric movement metric {key!r} on clip {clip_name}")
    return float(value)


def _read_glb_animation_names(glb_path: Path) -> list[str]:
    payload = glb_path.read_bytes()
    if len(payload) < 20:
        raise AssertionError(f"Invalid GLB header size: {glb_path}")
    magic, version, total_length = struct.unpack_from("<III", payload, 0)
    if magic != 0x46546C67:
        raise AssertionError(f"Invalid GLB magic for {glb_path}")
    if version != 2:
        raise AssertionError(f"Unsupported GLB version {version} for {glb_path}")
    if total_length != len(payload):
        raise AssertionError(f"GLB length mismatch for {glb_path}: header={total_length} bytes={len(payload)}")

    json_chunk: bytes | None = None
    offset = 12
    while offset + 8 <= len(payload):
        chunk_length, chunk_type = struct.unpack_from("<II", payload, offset)
        offset += 8
        end = offset + chunk_length
        if end > len(payload):
            raise AssertionError(f"Invalid GLB chunk length in {glb_path}")
        if chunk_type == 0x4E4F534A:
            json_chunk = payload[offset:end]
            break
        offset = end
    if json_chunk is None:
        raise AssertionError(f"GLB JSON chunk missing: {glb_path}")

    try:
        doc = json.loads(json_chunk.decode("utf-8").rstrip("\x00 \n\r\t"))
    except Exception as exc:  # pragma: no cover - defensive parse guard
        raise AssertionError(f"Failed to parse GLB JSON chunk: {glb_path}") from exc

    animations = doc.get("animations")
    if animations is None:
        return []
    if not isinstance(animations, list):
        raise AssertionError(f"GLB animations payload is not a list: {glb_path}")

    names: list[str] = []
    for idx, entry in enumerate(animations):
        if not isinstance(entry, dict):
            raise AssertionError(f"GLB animation entry #{idx} is not an object: {glb_path}")
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            raise AssertionError(f"GLB animation entry #{idx} missing name: {glb_path}")
        names.append(name.strip())
    return names


def _assert_glb_animation_contract(glb_path: Path, expected_names: list[str]) -> None:
    names = _read_glb_animation_names(glb_path)
    expected = {name.strip() for name in expected_names if name.strip()}
    actual = {name.strip() for name in names if name.strip()}
    if actual != expected:
        raise AssertionError(f"GLB animation names mismatch. expected={sorted(expected)} got={sorted(actual)}")
    nla_dupes = [name for name in names if name.startswith("NlaTrack")]
    if nla_dupes:
        raise AssertionError(f"GLB must not include NLA track animations: {sorted(nla_dupes)}")


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
    for clip in [f"{clip_name}.glb" for clip_name in RIFLE_LOCOMOTION_CLIPS]:
        _require(clip_dir / clip, f"animation clip {clip}")

    out_glb = root / "assets" / "models" / "tests" / "enemy_raider_01_movement_test.glb"
    out_lod1 = root / "assets" / "models" / "tests" / "enemy_raider_01_movement_test_lod1.glb"
    out_blend = root / "assets_pipeline" / "blender" / "enemy_raider_01_movement_test.blend"
    out_thumb = root / "assets" / "thumbnails" / "tests" / "enemy_raider_01_movement_test.png"
    out_report = root / "assets" / "reports" / "tests" / "enemy_raider_01_movement_test.json"
    out_log = root / "assets_pipeline" / "logs" / "enemy_raider_01_movement_test.log"

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
    for clip_name in RIFLE_LOCOMOTION_CLIPS:
        cmd.extend(["--anim_clip", f"{clip_name}={clip_dir / f'{clip_name}.glb'}"])
    for clip_name in RIFLE_LOCOMOTION_CLIPS:
        cmd.extend(["--requested_clip", clip_name])
    proc = subprocess.run(cmd, cwd=root, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"blender_runner failed with rc={proc.returncode}")
    _require(out_glb, "output glb")
    _assert_glb_animation_contract(out_glb, RIFLE_LOCOMOTION_CLIPS)

    report = json.loads(out_report.read_text(encoding="utf-8"))
    if report.get("status") != "ok":
        raise AssertionError(f"Expected status=ok, got {report.get('status')}")

    failures = report.get("movement_qc_failures")
    if not isinstance(failures, list):
        raise AssertionError("movement_qc_failures missing from report")
    if failures:
        raise AssertionError("movement_qc_failures should be empty: " + "; ".join(str(v) for v in failures))

    checked = report.get("locomotion_clips_checked")
    if not isinstance(checked, int) or checked < 4:
        raise AssertionError(f"Expected at least 4 locomotion clips checked, got {checked}")

    metrics = report.get("clip_motion_metrics")
    if not isinstance(metrics, dict):
        raise AssertionError("clip_motion_metrics missing from report")

    walk_span = _metric(metrics, "walk_rifle_in_place", "foot_span_avg_m")
    run_span = _metric(metrics, "run_rifle_in_place", "foot_span_avg_m")
    strafe_span = _metric(metrics, "strafe_left_rifle_in_place", "foot_span_avg_m")

    if walk_span < 0.08:
        raise AssertionError(f"walk_rifle_in_place foot_span_avg_m too low: {walk_span:.3f}m")
    if run_span < 0.10:
        raise AssertionError(f"run_rifle_in_place foot_span_avg_m too low: {run_span:.3f}m")
    if strafe_span < 0.08:
        raise AssertionError(f"strafe_left_rifle_in_place foot_span_avg_m too low: {strafe_span:.3f}m")
    if run_span < walk_span * 1.05:
        raise AssertionError(
            f"run span should exceed walk span by >=5% (run={run_span:.3f}m walk={walk_span:.3f}m)"
        )

    for clip_name in RIFLE_LOCOMOTION_CLIPS:
        drift = _metric(metrics, clip_name, "root_local_xy_max_m")
        if drift > 0.02:
            raise AssertionError(f"{clip_name} root_local_xy_max_m should be <= 0.02m, got {drift:.4f}m")

    print("raider movement qc regression test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
