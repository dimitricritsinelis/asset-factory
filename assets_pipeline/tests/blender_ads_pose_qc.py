#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import bpy
from mathutils import Vector


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect generated ADS blend output and emit QC JSON.")
    parser.add_argument("--out_json", required=True)
    parser.add_argument("--weapon_socket_bone", default="weapon_socket_r")
    parser.add_argument("--weapon_muzzle_object", default="muzzle")
    parser.add_argument("--head_bone", default="Head")
    parser.add_argument("--left_hand_bone", default="L_Hand")
    parser.add_argument("--right_hand_bone", default="R_Hand")
    parser.add_argument("--left_shoulder_bone", default="L_Clavicle")
    parser.add_argument("--right_shoulder_bone", default="R_Clavicle")
    parser.add_argument("--left_upperarm_bone", default="L_Upperarm")
    parser.add_argument("--left_forearm_bone", default="L_Forearm")
    parser.add_argument("--right_upperarm_bone", default="R_Upperarm")
    parser.add_argument("--right_forearm_bone", default="R_Forearm")
    parser.add_argument("--grip_right_offset_m", required=True)
    parser.add_argument("--grip_left_offset_m", required=True)
    parser.add_argument("--sight_offset_m", required=True)
    parser.add_argument("--ads_eye_offset_m", default="0,0,0")
    parser.add_argument("--left_grip_error_cm_max", type=float, default=3.0)
    parser.add_argument("--right_grip_error_cm_max", type=float, default=10.0)
    parser.add_argument("--ads_eye_to_sight_m_max", type=float, default=0.20)
    parser.add_argument("--ads_eye_weapon_alignment_deg_max", type=float, default=15.0)
    parser.add_argument("--left_hand_to_shoulder_m_max", type=float, default=0.85)
    parser.add_argument("--right_hand_to_shoulder_m_max", type=float, default=0.85)
    parser.add_argument("--left_elbow_angle_deg_min", type=float, default=10.0)
    parser.add_argument("--left_elbow_angle_deg_max", type=float, default=175.0)
    parser.add_argument("--right_elbow_angle_deg_min", type=float, default=10.0)
    parser.add_argument("--right_elbow_angle_deg_max", type=float, default=175.0)
    parser.add_argument("--max_hand_frame_delta_m", type=float, default=0.30)
    parser.add_argument("--clip", action="append", default=[])
    return parser.parse_args(argv)


def _parse_vec3(text: str) -> Vector:
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 3:
        raise ValueError(f"Expected vec3 with 3 components, got {text!r}")
    return Vector((float(parts[0]), float(parts[1]), float(parts[2])))


def _triangle_count(obj: bpy.types.Object) -> int:
    if obj.type != "MESH" or obj.data is None:
        return 0
    return sum(max(0, len(poly.vertices) - 2) for poly in obj.data.polygons)


def _first_armature() -> bpy.types.Object | None:
    for obj in bpy.data.objects:
        if obj.type == "ARMATURE":
            return obj
    return None


def _resolve_weapon_mesh(armature_obj: bpy.types.Object, socket_bone_name: str) -> bpy.types.Object | None:
    candidates = [
        obj
        for obj in bpy.data.objects
        if obj.type == "MESH"
        and obj.parent == armature_obj
        and obj.parent_type == "BONE"
    ]
    if not candidates:
        return None
    by_socket = [obj for obj in candidates if obj.parent_bone == socket_bone_name]
    target = by_socket if by_socket else candidates
    return max(target, key=_triangle_count)


def _resolve_muzzle_object(name: str) -> bpy.types.Object | None:
    if name in bpy.data.objects:
        return bpy.data.objects[name]
    for obj in bpy.data.objects:
        if obj.type == "EMPTY" and obj.name.lower() == name.lower():
            return obj
    return None


def _ads_sample_frames(frame_start: int, frame_end: int) -> list[int]:
    if frame_end < frame_start:
        return [frame_start]
    frame_count = frame_end - frame_start + 1
    if frame_count <= 80:
        return list(range(frame_start, frame_end + 1))
    sample_count = min(60, frame_count)
    if sample_count <= 1:
        return [frame_start, frame_end] if frame_end != frame_start else [frame_start]
    span = max(1, frame_end - frame_start)
    samples: set[int] = {frame_start, frame_end}
    for i in range(sample_count):
        t = float(i) / float(sample_count - 1)
        samples.add(frame_start + int(round(span * t)))
    return sorted(v for v in samples if frame_start <= v <= frame_end)


def _non_unit_scale_bones(armature_obj: bpy.types.Object, eps: float = 1e-4) -> list[str]:
    names: list[str] = []
    for pbone in armature_obj.pose.bones:
        sx, sy, sz = pbone.scale
        if abs(sx - 1.0) > eps or abs(sy - 1.0) > eps or abs(sz - 1.0) > eps:
            names.append(pbone.name)
    return names


def _clip_metrics(
    *,
    armature_obj: bpy.types.Object,
    weapon_obj: bpy.types.Object,
    muzzle_obj: bpy.types.Object | None,
    clip_name: str,
    left_hand_bone: str,
    right_hand_bone: str,
    head_bone: str,
    left_shoulder_bone: str,
    right_shoulder_bone: str,
    left_upperarm_bone: str,
    left_forearm_bone: str,
    right_upperarm_bone: str,
    right_forearm_bone: str,
    grip_right_local: Vector,
    grip_left_local: Vector,
    sight_local: Vector,
    ads_eye_offset_world: Vector,
    max_hand_frame_delta_m: float,
) -> dict:
    action = bpy.data.actions.get(clip_name)
    if action is None:
        return {"clip": clip_name, "missing": True}

    ad = armature_obj.animation_data
    if ad is None:
        armature_obj.animation_data_create()
        ad = armature_obj.animation_data
    assert ad is not None
    ad.action = action
    if hasattr(ad, "use_nla"):
        ad.use_nla = False

    frame_start = int(max(1.0, float(action.frame_range[0])))
    frame_end = int(max(frame_start + 1, float(action.frame_range[1])))
    sample_frames = _ads_sample_frames(frame_start, frame_end)

    def _safe_angle_deg(a: Vector, b: Vector) -> float:
        if a.length < 1e-8 or b.length < 1e-8:
            return 180.0
        av = a.normalized()
        bv = b.normalized()
        return math.degrees(av.angle(bv))

    left_max = 0.0
    right_max = 0.0
    ads_eye_to_sight_max = 0.0
    ads_eye_weapon_alignment_max = 0.0
    left_hand_shoulder_max = 0.0
    right_hand_shoulder_max = 0.0
    left_elbow_angle_min = 180.0
    left_elbow_angle_max = 0.0
    right_elbow_angle_min = 180.0
    right_elbow_angle_max = 0.0
    hand_frame_delta_max = 0.0
    prev_left_world: Vector | None = None
    prev_right_world: Vector | None = None
    samples: list[dict] = []
    scene = bpy.context.scene
    for frame in sample_frames:
        scene.frame_set(frame)
        left_world = (armature_obj.matrix_world @ armature_obj.pose.bones[left_hand_bone].matrix).translation
        right_world = (armature_obj.matrix_world @ armature_obj.pose.bones[right_hand_bone].matrix).translation
        head_world = (armature_obj.matrix_world @ armature_obj.pose.bones[head_bone].matrix).translation
        eye_world = head_world + ads_eye_offset_world
        left_shoulder_world = (armature_obj.matrix_world @ armature_obj.pose.bones[left_shoulder_bone].matrix).translation
        right_shoulder_world = (armature_obj.matrix_world @ armature_obj.pose.bones[right_shoulder_bone].matrix).translation
        left_upperarm_world = (armature_obj.matrix_world @ armature_obj.pose.bones[left_upperarm_bone].matrix).translation
        left_forearm_world = (armature_obj.matrix_world @ armature_obj.pose.bones[left_forearm_bone].matrix).translation
        right_upperarm_world = (armature_obj.matrix_world @ armature_obj.pose.bones[right_upperarm_bone].matrix).translation
        right_forearm_world = (armature_obj.matrix_world @ armature_obj.pose.bones[right_forearm_bone].matrix).translation
        grip_r_world = weapon_obj.matrix_world @ grip_right_local
        grip_l_world = weapon_obj.matrix_world @ grip_left_local
        sight_world = weapon_obj.matrix_world @ sight_local
        muzzle_world = muzzle_obj.matrix_world.translation if muzzle_obj is not None else (weapon_obj.matrix_world @ (sight_local + Vector((0.0, 1.0, 0.0))))

        left_err = (left_world - grip_l_world).length * 100.0
        right_err = (right_world - grip_r_world).length * 100.0
        eye_to_sight = sight_world - eye_world
        ads_eye_to_sight = eye_to_sight.length
        weapon_forward = muzzle_world - sight_world
        ads_eye_weapon_alignment = _safe_angle_deg(weapon_forward, eye_to_sight)
        left_hand_shoulder = (left_world - left_shoulder_world).length
        right_hand_shoulder = (right_world - right_shoulder_world).length

        left_elbow_angle = _safe_angle_deg(left_upperarm_world - left_forearm_world, left_world - left_forearm_world)
        right_elbow_angle = _safe_angle_deg(
            right_upperarm_world - right_forearm_world,
            right_world - right_forearm_world,
        )

        if prev_left_world is not None:
            hand_frame_delta_max = max(
                hand_frame_delta_max,
                (left_world - prev_left_world).length,
                (right_world - prev_right_world).length if prev_right_world is not None else 0.0,
            )
        prev_left_world = left_world.copy()
        prev_right_world = right_world.copy()

        left_max = max(left_max, left_err)
        right_max = max(right_max, right_err)
        ads_eye_to_sight_max = max(ads_eye_to_sight_max, ads_eye_to_sight)
        ads_eye_weapon_alignment_max = max(ads_eye_weapon_alignment_max, ads_eye_weapon_alignment)
        left_hand_shoulder_max = max(left_hand_shoulder_max, left_hand_shoulder)
        right_hand_shoulder_max = max(right_hand_shoulder_max, right_hand_shoulder)
        left_elbow_angle_min = min(left_elbow_angle_min, left_elbow_angle)
        left_elbow_angle_max = max(left_elbow_angle_max, left_elbow_angle)
        right_elbow_angle_min = min(right_elbow_angle_min, right_elbow_angle)
        right_elbow_angle_max = max(right_elbow_angle_max, right_elbow_angle)
        samples.append(
            {
                "frame": frame,
                "left_grip_error_cm": left_err,
                "right_grip_error_cm": right_err,
                "ads_eye_to_sight_m": ads_eye_to_sight,
                "ads_eye_weapon_alignment_deg": ads_eye_weapon_alignment,
                "left_hand_to_shoulder_m": left_hand_shoulder,
                "right_hand_to_shoulder_m": right_hand_shoulder,
                "left_elbow_angle_deg": left_elbow_angle,
                "right_elbow_angle_deg": right_elbow_angle,
            }
        )

    return {
        "clip": clip_name,
        "missing": False,
        "frame_start": frame_start,
        "frame_end": frame_end,
        "sample_frames": sample_frames,
        "left_grip_error_cm_max": left_max,
        "right_grip_error_cm_max": right_max,
        "ads_eye_to_sight_m_max": ads_eye_to_sight_max,
        "ads_eye_weapon_alignment_deg_max": ads_eye_weapon_alignment_max,
        "left_hand_to_shoulder_m_max": left_hand_shoulder_max,
        "right_hand_to_shoulder_m_max": right_hand_shoulder_max,
        "left_elbow_angle_deg_min": left_elbow_angle_min,
        "left_elbow_angle_deg_max": left_elbow_angle_max,
        "right_elbow_angle_deg_min": right_elbow_angle_min,
        "right_elbow_angle_deg_max": right_elbow_angle_max,
        "hand_frame_delta_m_max": hand_frame_delta_max,
        "frame_count": max(0, frame_end - frame_start + 1),
        "samples": samples,
    }


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    out_path = Path(args.out_json)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    result: dict = {
        "status": "ok",
        "failures": [],
        "armature_name": "",
        "weapon_mesh_name": "",
        "animation_use_nla": None,
        "non_unit_scale_bones": [],
        "clip_metrics": [],
        "thresholds": {
            "left_grip_error_cm_max": float(args.left_grip_error_cm_max),
            "right_grip_error_cm_max": float(args.right_grip_error_cm_max),
            "ads_eye_to_sight_m_max": float(args.ads_eye_to_sight_m_max),
            "ads_eye_weapon_alignment_deg_max": float(args.ads_eye_weapon_alignment_deg_max),
            "left_hand_to_shoulder_m_max": float(args.left_hand_to_shoulder_m_max),
            "right_hand_to_shoulder_m_max": float(args.right_hand_to_shoulder_m_max),
            "left_elbow_angle_deg_min": float(args.left_elbow_angle_deg_min),
            "left_elbow_angle_deg_max": float(args.left_elbow_angle_deg_max),
            "right_elbow_angle_deg_min": float(args.right_elbow_angle_deg_min),
            "right_elbow_angle_deg_max": float(args.right_elbow_angle_deg_max),
            "max_hand_frame_delta_m": float(args.max_hand_frame_delta_m),
        },
    }

    try:
        armature = _first_armature()
        if armature is None:
            result["failures"].append("armature missing in blend")
            raise RuntimeError("armature missing")
        result["armature_name"] = armature.name

        ad = armature.animation_data
        result["animation_use_nla"] = bool(getattr(ad, "use_nla", False)) if ad is not None else None
        if result["animation_use_nla"] is True:
            result["failures"].append("animation_data.use_nla must be false for single-clip ADS review")

        for bone_name in [
            args.left_hand_bone,
            args.right_hand_bone,
            args.head_bone,
            args.left_shoulder_bone,
            args.right_shoulder_bone,
            args.left_upperarm_bone,
            args.left_forearm_bone,
            args.right_upperarm_bone,
            args.right_forearm_bone,
        ]:
            if bone_name not in armature.pose.bones:
                result["failures"].append(f"missing pose bone: {bone_name}")

        weapon = _resolve_weapon_mesh(armature, args.weapon_socket_bone)
        if weapon is None:
            result["failures"].append("weapon mesh parented to armature bone not found")
            raise RuntimeError("weapon missing")
        result["weapon_mesh_name"] = weapon.name
        muzzle = _resolve_muzzle_object(args.weapon_muzzle_object)
        if muzzle is None:
            result["failures"].append(f"muzzle object missing: {args.weapon_muzzle_object}")

        non_unit = _non_unit_scale_bones(armature)
        result["non_unit_scale_bones"] = non_unit
        if non_unit:
            result["failures"].append("non-unit pose bone scales detected: " + ", ".join(non_unit[:10]))

        grip_r = _parse_vec3(args.grip_right_offset_m)
        grip_l = _parse_vec3(args.grip_left_offset_m)
        sight = _parse_vec3(args.sight_offset_m)
        ads_eye_offset = _parse_vec3(args.ads_eye_offset_m)

        for clip_name in [c for c in args.clip if c.strip()]:
            metrics = _clip_metrics(
                armature_obj=armature,
                weapon_obj=weapon,
                muzzle_obj=muzzle,
                clip_name=clip_name.strip(),
                left_hand_bone=args.left_hand_bone,
                right_hand_bone=args.right_hand_bone,
                head_bone=args.head_bone,
                left_shoulder_bone=args.left_shoulder_bone,
                right_shoulder_bone=args.right_shoulder_bone,
                left_upperarm_bone=args.left_upperarm_bone,
                left_forearm_bone=args.left_forearm_bone,
                right_upperarm_bone=args.right_upperarm_bone,
                right_forearm_bone=args.right_forearm_bone,
                grip_right_local=grip_r,
                grip_left_local=grip_l,
                sight_local=sight,
                ads_eye_offset_world=ads_eye_offset,
                max_hand_frame_delta_m=float(args.max_hand_frame_delta_m),
            )
            result["clip_metrics"].append(metrics)
            if metrics.get("missing"):
                result["failures"].append(f"missing action in blend: {clip_name}")
                continue
            if float(metrics["left_grip_error_cm_max"]) > float(args.left_grip_error_cm_max):
                result["failures"].append(
                    f"{clip_name}: left_grip_error_cm_max={metrics['left_grip_error_cm_max']:.2f}"
                    f" (>{args.left_grip_error_cm_max:.2f})"
                )
            if float(metrics["right_grip_error_cm_max"]) > float(args.right_grip_error_cm_max):
                result["failures"].append(
                    f"{clip_name}: right_grip_error_cm_max={metrics['right_grip_error_cm_max']:.2f}"
                    f" (>{args.right_grip_error_cm_max:.2f})"
                )
            if float(metrics["ads_eye_to_sight_m_max"]) > float(args.ads_eye_to_sight_m_max):
                result["failures"].append(
                    f"{clip_name}: ads_eye_to_sight_m_max={metrics['ads_eye_to_sight_m_max']:.3f}"
                    f" (>{args.ads_eye_to_sight_m_max:.3f})"
                )
            if float(metrics["ads_eye_weapon_alignment_deg_max"]) > float(args.ads_eye_weapon_alignment_deg_max):
                result["failures"].append(
                    f"{clip_name}: ads_eye_weapon_alignment_deg_max={metrics['ads_eye_weapon_alignment_deg_max']:.2f}"
                    f" (>{args.ads_eye_weapon_alignment_deg_max:.2f})"
                )
            if float(metrics["left_hand_to_shoulder_m_max"]) > float(args.left_hand_to_shoulder_m_max):
                result["failures"].append(
                    f"{clip_name}: left_hand_to_shoulder_m_max={metrics['left_hand_to_shoulder_m_max']:.3f}"
                    f" (>{args.left_hand_to_shoulder_m_max:.3f})"
                )
            if float(metrics["right_hand_to_shoulder_m_max"]) > float(args.right_hand_to_shoulder_m_max):
                result["failures"].append(
                    f"{clip_name}: right_hand_to_shoulder_m_max={metrics['right_hand_to_shoulder_m_max']:.3f}"
                    f" (>{args.right_hand_to_shoulder_m_max:.3f})"
                )
            if float(metrics["left_elbow_angle_deg_min"]) < float(args.left_elbow_angle_deg_min):
                result["failures"].append(
                    f"{clip_name}: left_elbow_angle_deg_min={metrics['left_elbow_angle_deg_min']:.2f}"
                    f" (<{args.left_elbow_angle_deg_min:.2f})"
                )
            if float(metrics["left_elbow_angle_deg_max"]) > float(args.left_elbow_angle_deg_max):
                result["failures"].append(
                    f"{clip_name}: left_elbow_angle_deg_max={metrics['left_elbow_angle_deg_max']:.2f}"
                    f" (>{args.left_elbow_angle_deg_max:.2f})"
                )
            if float(metrics["right_elbow_angle_deg_min"]) < float(args.right_elbow_angle_deg_min):
                result["failures"].append(
                    f"{clip_name}: right_elbow_angle_deg_min={metrics['right_elbow_angle_deg_min']:.2f}"
                    f" (<{args.right_elbow_angle_deg_min:.2f})"
                )
            if float(metrics["right_elbow_angle_deg_max"]) > float(args.right_elbow_angle_deg_max):
                result["failures"].append(
                    f"{clip_name}: right_elbow_angle_deg_max={metrics['right_elbow_angle_deg_max']:.2f}"
                    f" (>{args.right_elbow_angle_deg_max:.2f})"
                )
            if float(metrics["hand_frame_delta_m_max"]) > float(args.max_hand_frame_delta_m):
                result["failures"].append(
                    f"{clip_name}: hand_frame_delta_m_max={metrics['hand_frame_delta_m_max']:.3f}"
                    f" (>{args.max_hand_frame_delta_m:.3f})"
                )
    except Exception as exc:  # noqa: BLE001
        result["failures"].append(f"blend qc runtime error: {exc}")

    result["failures"] = list(dict.fromkeys(str(v) for v in result["failures"] if str(v).strip()))
    if result["failures"]:
        result["status"] = "failed"
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    import sys

    if "--" in sys.argv:
        payload = sys.argv[sys.argv.index("--") + 1 :]
    else:
        payload = []
    raise SystemExit(main(payload))
