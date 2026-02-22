#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import struct
import subprocess
from pathlib import Path


ADS_CLIPS = [
    "idle_rifle_in_place",
    "walk_rifle_in_place",
    "run_rifle_in_place",
    "strafe_left_rifle_in_place",
]


def _require(path: Path, label: str) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Missing {label}: {path}")


def _num(payload: dict, key: str) -> float:
    value = payload.get(key)
    if not isinstance(value, (int, float)):
        raise AssertionError(f"Missing numeric field {key!r}")
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


def _run_blend_pose_qc(
    *,
    root: Path,
    blender_bin: Path,
    blend_path: Path,
    clips: list[str],
) -> dict:
    script = root / "assets_pipeline" / "tests" / "blender_ads_pose_qc.py"
    _require(script, "blend pose qc script")
    qc_json = root / "assets" / "reports" / "tests" / "enemy_raider_01_ads_test_blend_qc.json"
    qc_json.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        str(blender_bin),
        "-b",
        str(blend_path),
        "--python",
        str(script),
        "--",
        "--out_json",
        str(qc_json),
        "--weapon_socket_bone",
        "weapon_socket_r",
        "--weapon_muzzle_object",
        "muzzle",
        "--head_bone",
        "Head",
        "--left_hand_bone",
        "L_Hand",
        "--right_hand_bone",
        "R_Hand",
        "--grip_right_offset_m=-0.034,0.054,0.25",
        "--grip_left_offset_m=-0.05,0.63,0.21",
        "--sight_offset_m=-0.10,0.24,0.52",
        "--left_grip_error_cm_max",
        "3.0",
        "--right_grip_error_cm_max",
        "10.0",
        "--ads_eye_to_sight_m_max",
        "0.20",
        "--ads_eye_weapon_alignment_deg_max",
        "180.0",
        "--left_hand_to_shoulder_m_max",
        "0.85",
        "--right_hand_to_shoulder_m_max",
        "0.85",
        "--left_elbow_angle_deg_min",
        "10.0",
        "--left_elbow_angle_deg_max",
        "178.0",
        "--right_elbow_angle_deg_min",
        "10.0",
        "--right_elbow_angle_deg_max",
        "178.0",
        "--max_hand_frame_delta_m",
        "0.60",
    ]
    for clip in clips:
        cmd.extend(["--clip", clip])
    proc = subprocess.run(cmd, cwd=root, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"blender ads pose qc failed with rc={proc.returncode}")
    _require(qc_json, "blend pose qc report")
    return json.loads(qc_json.read_text(encoding="utf-8"))


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
    for clip in ADS_CLIPS:
        _require(clip_dir / f"{clip}.glb", f"animation clip {clip}.glb")

    out_glb = root / "assets" / "models" / "tests" / "enemy_raider_01_ads_test.glb"
    out_lod1 = root / "assets" / "models" / "tests" / "enemy_raider_01_ads_test_lod1.glb"
    out_blend = root / "assets_pipeline" / "blender" / "enemy_raider_01_ads_test.blend"
    out_thumb = root / "assets" / "thumbnails" / "tests" / "enemy_raider_01_ads_test.png"
    out_report = root / "assets" / "reports" / "tests" / "enemy_raider_01_ads_test.json"
    out_log = root / "assets_pipeline" / "logs" / "enemy_raider_01_ads_test.log"

    for path in [out_glb, out_lod1, out_blend, out_thumb, out_report, out_log]:
        path.parent.mkdir(parents=True, exist_ok=True)

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
        "--grip_right_offset_m=-0.034,0.054,0.25",
        "--grip_left_offset_m=-0.05,0.63,0.21",
        "--sight_offset_m=-0.10,0.24,0.52",
        "--ads_enabled",
        "--ads_aim_distance_m",
        "12.0",
        "--ads_head_bone",
        "Head",
        "--ads_spine_bone",
        "Spine01",
        "--ads_spine_bone",
        "Spine02",
        "--ads_spine_bone",
        "R_Clavicle",
        "--ads_spine_bone",
        "NeckTwist01",
        "--ads_spine_bone",
        "NeckTwist02",
    ]
    for clip in ADS_CLIPS:
        cmd.extend(["--anim_clip", f"{clip}={clip_dir / f'{clip}.glb'}"])
    for clip in ADS_CLIPS:
        cmd.extend(["--requested_clip", clip])
    for clip in ADS_CLIPS:
        cmd.extend(["--ads_clip", clip])

    proc = subprocess.run(cmd, cwd=root, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"blender_runner failed with rc={proc.returncode}")
    _require(out_glb, "output glb")
    _require(out_lod1, "output lod1 glb")
    _assert_glb_animation_contract(out_glb, ADS_CLIPS)
    _assert_glb_animation_contract(out_lod1, ADS_CLIPS)

    report = json.loads(out_report.read_text(encoding="utf-8"))
    if report.get("status") != "ok":
        raise AssertionError(f"Expected status=ok, got {report.get('status')}")

    if report.get("ads_enabled") is not True:
        raise AssertionError("ads_enabled should be true")

    ads_failures = report.get("ads_qc_failures")
    if not isinstance(ads_failures, list):
        raise AssertionError("ads_qc_failures missing from report")
    if ads_failures:
        raise AssertionError("ads_qc_failures should be empty: " + "; ".join(str(v) for v in ads_failures))

    targeted = report.get("ads_clips_targeted")
    if not isinstance(targeted, list):
        raise AssertionError("ads_clips_targeted missing from report")
    baked = report.get("ads_clips_baked")
    if not isinstance(baked, list):
        raise AssertionError("ads_clips_baked missing from report")

    targeted_set = {str(name).strip().lower() for name in targeted if str(name).strip()}
    baked_set = {str(name).strip().lower() for name in baked if str(name).strip()}
    expected_set = {name.lower() for name in ADS_CLIPS}
    if targeted_set != expected_set:
        raise AssertionError(
            f"ads_clips_targeted mismatch: expected {sorted(expected_set)} got {sorted(targeted_set)}"
        )
    if not expected_set.issubset(baked_set):
        raise AssertionError(
            f"ads_clips_baked missing clips: expected {sorted(expected_set)} got {sorted(baked_set)}"
        )

    exported_clip_names = report.get("clip_names")
    if not isinstance(exported_clip_names, list):
        raise AssertionError("clip_names missing from report")
    clip_name_set = {str(name).strip().lower() for name in exported_clip_names if str(name).strip()}
    if clip_name_set != expected_set:
        raise AssertionError(f"clip_names mismatch: expected {sorted(expected_set)} got {sorted(clip_name_set)}")

    left_error = _num(report, "left_hand_grip_error_cm_max")
    eye_to_sight = _num(report, "ads_eye_to_sight_m_max")
    alignment = _num(report, "ads_eye_weapon_alignment_deg_max")
    if left_error > 3.0:
        raise AssertionError(f"left_hand_grip_error_cm_max must be <= 3.0, got {left_error:.2f}")
    if eye_to_sight > 0.20:
        raise AssertionError(f"ads_eye_to_sight_m_max must be <= 0.20, got {eye_to_sight:.3f}")
    if alignment > 15.0:
        raise AssertionError(f"ads_eye_weapon_alignment_deg_max must be <= 15.0, got {alignment:.2f}")

    clip_metrics = report.get("ads_clip_metrics")
    if not isinstance(clip_metrics, list):
        raise AssertionError("ads_clip_metrics missing from report")
    metric_clips = {str(item.get("clip", "")).strip().lower() for item in clip_metrics if isinstance(item, dict)}
    if metric_clips != expected_set:
        raise AssertionError(
            f"ads_clip_metrics clip coverage mismatch: expected {sorted(expected_set)} got {sorted(metric_clips)}"
        )

    exported_names = [str(name) for name in report.get("exported_object_names", [])]
    if any(name in {"grip_r", "grip_l"} for name in exported_names):
        raise AssertionError("Internal grip helpers must not be exported")

    blend_qc = _run_blend_pose_qc(
        root=root,
        blender_bin=blender_bin,
        blend_path=out_blend,
        clips=ADS_CLIPS,
    )
    if blend_qc.get("status") != "ok":
        failures = blend_qc.get("failures")
        if isinstance(failures, list) and failures:
            raise AssertionError("blend pose qc failed: " + "; ".join(str(v) for v in failures))
        raise AssertionError(f"blend pose qc failed: {blend_qc!r}")

    print("raider ads qc regression test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
