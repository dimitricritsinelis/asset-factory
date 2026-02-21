import argparse
import importlib.util
import json
import math
from pathlib import Path
import sys
import time
import traceback

import bpy
from mathutils import Euler, Matrix, Vector


def _load_blender_ops():
    script_dir = Path(__file__).resolve().parent
    ops_path = script_dir / "blender_ops.py"
    spec = importlib.util.spec_from_file_location("blender_ops", str(ops_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load blender_ops from {ops_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run constrained Blender build/export")
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument("--recipe", required=False)
    mode_group.add_argument("--input_model", required=False)

    parser.add_argument("--out", required=True)
    parser.add_argument("--out_lod1", required=False)
    parser.add_argument("--blend", required=True)
    parser.add_argument("--thumb", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--log", required=False)

    parser.add_argument("--target_height_m", type=float, required=False)
    parser.add_argument("--target_length_m", type=float, required=False)
    parser.add_argument("--target_tris", type=int, required=False)
    parser.add_argument("--target_tris_lod1", type=int, required=False)
    parser.add_argument("--mode", choices=["generic", "character", "weapon"], default="generic")
    parser.add_argument("--primary_color", type=str, required=False, default="#A8A8A8")
    parser.add_argument("--secondary_color", type=str, required=False, default="#4F5A4D")
    parser.add_argument("--anim_model", action="append", default=[], help="Additional animation source GLB")
    parser.add_argument(
        "--anim_clip",
        action="append",
        default=[],
        help="Additional animation source in output_name=path form",
    )
    parser.add_argument("--requested_clip", action="append", default=[], help="Requested output clip names")
    parser.add_argument("--retarget_map", type=str, required=False, help="Optional retarget mapping JSON path")
    parser.add_argument("--canonical_pose", choices=["T", "none"], default="T")
    parser.add_argument("--in_place", action="store_true", help="Zero root-bone XY translation in imported actions")
    parser.add_argument(
        "--aggressive_character_fix",
        action="store_true",
        help="Apply aggressive cleanup/retarget passes for enforced character QC retry",
    )
    parser.add_argument(
        "--character_qc_enforced",
        action="store_true",
        help="Report character QC enforcement mode for downstream gates",
    )
    parser.add_argument("--character", action="store_true", help="Enable character-safe material defaults")
    parser.add_argument("--weapon_model", type=str, required=False, help="Weapon GLB to embed into character")
    parser.add_argument("--weapon_socket_bone_name", type=str, default="weapon_socket_r")
    parser.add_argument("--weapon_bone_semantic", type=str, default="right_hand")
    parser.add_argument("--weapon_muzzle_socket_name", type=str, default="muzzle")
    parser.add_argument("--weapon_offset_m", type=str, default="0,0,0")
    parser.add_argument("--weapon_rotation_deg", type=str, default="0,0,0")
    parser.add_argument("--weapon_scale", type=float, default=1.0)
    parser.add_argument("--grip_right_offset_m", type=str, default="")
    parser.add_argument("--grip_left_offset_m", type=str, default="")
    parser.add_argument("--sight_offset_m", type=str, default="")
    parser.add_argument("--ads_enabled", action="store_true")
    parser.add_argument("--ads_clip", action="append", default=[])
    parser.add_argument("--ads_aim_distance_m", type=float, default=12.0)
    parser.add_argument("--ads_eye_offset_m", type=str, default="")
    parser.add_argument("--ads_head_bone", type=str, default="")
    parser.add_argument("--ads_spine_bone", action="append", default=[])
    return parser.parse_args(argv)


def _write_report(report_path: Path, payload: dict) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _color_to_rgba(color: str, fallback: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    c = color.strip().lower()
    named: dict[str, tuple[float, float, float, float]] = {
        "dusty sand": (0.76, 0.66, 0.48, 1.0),
        "dark olive": (0.25, 0.29, 0.22, 1.0),
        "sand": (0.78, 0.71, 0.55, 1.0),
        "olive": (0.33, 0.39, 0.27, 1.0),
        "gray": (0.6, 0.6, 0.6, 1.0),
    }
    if c in named:
        return named[c]
    if c.startswith("#"):
        hx = c[1:]
        if len(hx) == 3:
            hx = "".join(ch * 2 for ch in hx)
        if len(hx) == 6:
            try:
                r = int(hx[0:2], 16) / 255.0
                g = int(hx[2:4], 16) / 255.0
                b = int(hx[4:6], 16) / 255.0
                return (r, g, b, 1.0)
            except ValueError:
                pass
    return fallback


def _parse_vec3(raw: str, default: tuple[float, float, float]) -> tuple[float, float, float]:
    try:
        parts = [float(x.strip()) for x in raw.split(",")]
        if len(parts) != 3:
            return default
        return (parts[0], parts[1], parts[2])
    except Exception:
        return default


def _parse_optional_vec3(raw: str | None) -> tuple[float, float, float] | None:
    if raw is None:
        return None
    text = raw.strip()
    if not text or text.lower() in {"none", "null"}:
        return None
    sentinel = (float("nan"), float("nan"), float("nan"))
    parsed = _parse_vec3(text, sentinel)
    if any(math.isnan(v) for v in parsed):
        return None
    return parsed


def _find_right_hand_bone_name(armature_obj: bpy.types.Object) -> str | None:
    if armature_obj.type != "ARMATURE" or armature_obj.data is None:
        return None
    bones = list(armature_obj.data.bones)
    if not bones:
        return None

    names = {b.name.lower(): b.name for b in bones}
    exact = [
        "mixamorig:righthand",
        "righthand",
        "hand.r",
        "right_hand",
        "bip001 r hand",
        "bip_r_hand",
    ]
    for key in exact:
        if key in names:
            return names[key]

    for bone in bones:
        n = bone.name.lower()
        # Handle common right-hand naming variants (R_Hand, hand_r, RightHand, mixamo forms).
        if "hand" in n and (
            "right" in n
            or ".r" in n
            or "_r" in n
            or n.startswith("r_")
            or n.startswith("rhand")
            or n.startswith("r-hand")
            or n.endswith("_right")
            or n.endswith(".right")
        ):
            return bone.name
    return None


def _object_exists(obj: bpy.types.Object | None) -> bool:
    try:
        return bool(obj is not None and obj.name in bpy.data.objects)
    except ReferenceError:
        return False


def _disable_nla_evaluation(armature_obj: bpy.types.Object | None) -> None:
    if not _object_exists(armature_obj):
        return
    if armature_obj.type != "ARMATURE":
        return
    ad = getattr(armature_obj, "animation_data", None)
    if ad is not None and hasattr(ad, "use_nla"):
        ad.use_nla = False


def _remove_all_nla_tracks(animation_data: bpy.types.AnimData | None) -> int:
    if animation_data is None:
        return 0
    tracks = getattr(animation_data, "nla_tracks", None)
    if tracks is None:
        return 0
    removed = 0
    for track in list(tracks):
        tracks.remove(track)
        removed += 1
    return removed


def _clear_armature_nla_tracks(armature_obj: bpy.types.Object | None) -> int:
    if not _object_exists(armature_obj):
        return 0
    if armature_obj.type != "ARMATURE":
        return 0
    return _remove_all_nla_tracks(getattr(armature_obj, "animation_data", None))


def _remove_default_nla_tracks(animation_data: bpy.types.AnimData | None) -> int:
    if animation_data is None:
        return 0
    removed = 0
    tracks = getattr(animation_data, "nla_tracks", None)
    if tracks is None:
        return 0
    for track in list(tracks):
        name = str(getattr(track, "name", "") or "")
        if name.startswith("NlaTrack") or name.startswith("[Action Stash]"):
            tracks.remove(track)
            removed += 1
    return removed


def _prune_default_nla_tracks(objects: list[bpy.types.Object] | None = None) -> int:
    candidates = objects if objects is not None else list(bpy.data.objects)
    removed = 0
    for obj in candidates:
        if not _object_exists(obj):
            continue
        removed += _remove_default_nla_tracks(getattr(obj, "animation_data", None))
        data = getattr(obj, "data", None)
        if data is not None:
            removed += _remove_default_nla_tracks(getattr(data, "animation_data", None))
            shape_keys = getattr(data, "shape_keys", None)
            if shape_keys is not None:
                removed += _remove_default_nla_tracks(getattr(shape_keys, "animation_data", None))
    return removed


def _ensure_socket_bone(
    armature_obj: bpy.types.Object,
    *,
    socket_bone_name: str,
    bone_semantic: str,
) -> str:
    if armature_obj.type != "ARMATURE":
        raise RuntimeError("Socket bone creation requires an armature object")

    parent_name: str | None = None
    if bone_semantic.strip().lower() == "right_hand":
        parent_name = _find_right_hand_bone_name(armature_obj)
    if parent_name is None and armature_obj.data.bones:
        parent_name = armature_obj.data.bones[0].name
    if parent_name is None:
        raise RuntimeError("Could not resolve a parent bone for socket creation")

    existing = armature_obj.data.bones.get(socket_bone_name)
    if existing is not None:
        current_parent = existing.parent.name if existing.parent is not None else None
        # Repair stale socket bones created in earlier runs with incorrect parents.
        if current_parent == parent_name:
            return socket_bone_name

    bpy.ops.object.select_all(action="DESELECT")
    armature_obj.select_set(True)
    bpy.context.view_layer.objects.active = armature_obj
    bpy.ops.object.mode_set(mode="EDIT")

    edit_bones = armature_obj.data.edit_bones
    parent = edit_bones.get(parent_name)
    if parent is None:
        bpy.ops.object.mode_set(mode="OBJECT")
        raise RuntimeError(f"Parent bone not found while creating socket: {parent_name}")
    socket = edit_bones.get(socket_bone_name)
    if socket is None:
        socket = edit_bones.new(socket_bone_name)
    socket.head = parent.tail.copy()
    socket.tail = parent.tail + Vector((0.0, 0.06, 0.0))
    socket.parent = parent
    socket.use_connect = False

    bpy.ops.object.mode_set(mode="OBJECT")
    return socket_bone_name


def _pick_muzzle_location(OPS, weapon_meshes: list[bpy.types.Object]) -> Vector:
    mins, maxs = OPS.bounds_min_max(weapon_meshes)
    length = max(maxs.y - mins.y, 0.15)
    return Vector(((mins.x + maxs.x) * 0.5, maxs.y + length * 0.03, (mins.z + maxs.z) * 0.5))


def _pick_grip_locations(OPS, weapon_meshes: list[bpy.types.Object]) -> tuple[Vector, Vector]:
    mins, maxs = OPS.bounds_min_max(weapon_meshes)
    cx = (mins.x + maxs.x) * 0.5
    cy = (mins.y + maxs.y) * 0.5
    cz = (mins.z + maxs.z) * 0.5
    span_y = max(maxs.y - mins.y, 0.15)
    grip_r = Vector((cx, cy - span_y * 0.2, cz))
    grip_l = Vector((cx, cy + span_y * 0.15, cz))
    return grip_r, grip_l


def _pick_sight_location(OPS, weapon_meshes: list[bpy.types.Object], muzzle_location: Vector) -> Vector:
    mins, maxs = OPS.bounds_min_max(weapon_meshes)
    cx = (mins.x + maxs.x) * 0.5
    cy = (mins.y + maxs.y) * 0.5
    cz = (mins.z + maxs.z) * 0.5
    span_y = max(maxs.y - mins.y, 0.15)
    span_z = max(maxs.z - mins.z, 0.05)
    center = Vector((cx, cy, cz))
    forward = muzzle_location - center
    if forward.length < 1e-6:
        forward = Vector((0.0, 1.0, 0.0))
    else:
        forward.normalize()
    return muzzle_location - (forward * (span_y * 0.55)) + Vector((0.0, 0.0, span_z * 0.12))


def _ensure_weapon_empty(
    *,
    name: str,
    location: Vector,
    imported_objects: list[bpy.types.Object],
) -> bpy.types.Object:
    empty = next((o for o in imported_objects if o.type == "EMPTY" and o.name == name), None)
    if empty is None:
        bpy.ops.object.empty_add(type="PLAIN_AXES", location=location)
        empty = bpy.context.active_object
        empty.name = name
    else:
        empty.location = location
    return empty


def _ensure_weapon_muzzle_empty(
    *,
    OPS,
    weapon_meshes: list[bpy.types.Object],
    imported_objects: list[bpy.types.Object],
    muzzle_name: str,
) -> bpy.types.Object:
    loc = _pick_muzzle_location(OPS, weapon_meshes)
    return _ensure_weapon_empty(name=muzzle_name, location=loc, imported_objects=imported_objects)


def _ensure_weapon_grip_empty(
    *,
    name: str,
    location: Vector,
    imported_objects: list[bpy.types.Object],
) -> bpy.types.Object:
    return _ensure_weapon_empty(name=name, location=location, imported_objects=imported_objects)


def _attach_weapon_to_character(
    *,
    OPS,
    armature: bpy.types.Object | None,
    fallback_anchor: bpy.types.Object | None,
    weapon_model_path: Path,
    socket_bone_name: str,
    bone_semantic: str,
    muzzle_name: str,
    offset_m: tuple[float, float, float],
    rotation_deg: tuple[float, float, float],
    uniform_scale: float,
    grip_right_offset_m: tuple[float, float, float] | None,
    grip_left_offset_m: tuple[float, float, float] | None,
    sight_offset_m: tuple[float, float, float] | None,
    ads_aim_distance_m: float,
) -> tuple[
    list[bpy.types.Object],
    bpy.types.Object | None,
    bpy.types.Object | None,
    bpy.types.Object | None,
    bpy.types.Object | None,
    bpy.types.Object | None,
    dict[str, str],
    list[str],
]:
    reasons: list[str] = []
    anchor_modes = {
        "grip_right_mode": "heuristic",
        "grip_left_mode": "heuristic",
        "sight_mode": "heuristic",
    }
    if not weapon_model_path.exists():
        return [], None, None, None, None, None, anchor_modes, [f"weapon embed skipped: model not found ({weapon_model_path})"]

    imported_objects = _import_new_objects(weapon_model_path)
    weapon_meshes = [o for o in imported_objects if o.type == "MESH"]
    if not weapon_meshes:
        for obj in imported_objects:
            if _object_exists(obj):
                bpy.data.objects.remove(obj, do_unlink=True)
        return [], None, None, None, None, None, anchor_modes, ["weapon embed skipped: imported weapon has no mesh objects"]

    imported_armatures = [o for o in imported_objects if o.type == "ARMATURE"]
    OPS.delete_objects(imported_armatures)

    for mesh in weapon_meshes:
        for mod in list(mesh.modifiers):
            if mod.type == "ARMATURE":
                mesh.modifiers.remove(mod)

    if armature is not None:
        socket_name = _ensure_socket_bone(
            armature,
            socket_bone_name=socket_bone_name,
            bone_semantic=bone_semantic,
        )
        pose_bone = armature.pose.bones.get(socket_name)
        if pose_bone is None:
            raise RuntimeError(f"Socket bone not found after creation: {socket_name}")
        bone_world = armature.matrix_world @ pose_bone.matrix
        offset_matrix = Matrix.Translation(Vector(offset_m))
        rotation_matrix = Euler(tuple(math.radians(v) for v in rotation_deg), "XYZ").to_matrix().to_4x4()
        scale_matrix = Matrix.Diagonal((uniform_scale, uniform_scale, uniform_scale, 1.0))
        attach_world = bone_world @ offset_matrix @ rotation_matrix @ scale_matrix
        group_origin = weapon_meshes[0].matrix_world.copy()
        for mesh in weapon_meshes:
            relative = group_origin.inverted() @ mesh.matrix_world
            target_world = attach_world @ relative
            mesh.parent = armature
            mesh.parent_type = "BONE"
            mesh.parent_bone = socket_name
            mesh.matrix_world = target_world
    else:
        reasons.append("weapon embedded without armature socket (fallback anchor)")
        anchor = fallback_anchor if fallback_anchor is not None else weapon_meshes[0]
        if fallback_anchor is not None:
            mins, maxs = OPS.bounds_min_max([fallback_anchor])
            anchor_point = Vector((maxs.x + 0.12, (mins.y + maxs.y) * 0.5, mins.z + (maxs.z - mins.z) * 0.6))
        else:
            anchor_point = Vector((0.0, 0.0, 1.0))
        for mesh in weapon_meshes:
            mesh.parent = anchor
            mesh.location = anchor_point + Vector(offset_m)
            mesh.rotation_mode = "XYZ"
            mesh.rotation_euler = tuple(math.radians(v) for v in rotation_deg)
            mesh.scale = (uniform_scale, uniform_scale, uniform_scale)

    valid_imported = [o for o in imported_objects if _object_exists(o)]
    primary_weapon_mesh = max(weapon_meshes, key=OPS.object_triangle_count)
    primary_world = primary_weapon_mesh.matrix_world.copy()
    primary_world_inv = primary_world.inverted()

    muzzle_world = _pick_muzzle_location(OPS, weapon_meshes)
    grip_r_world, grip_l_world = _pick_grip_locations(OPS, weapon_meshes)
    sight_world = _pick_sight_location(OPS, weapon_meshes, muzzle_world)

    muzzle_local = primary_world_inv @ muzzle_world
    if grip_right_offset_m is not None:
        grip_r_local = Vector(grip_right_offset_m)
        anchor_modes["grip_right_mode"] = "explicit"
    else:
        grip_r_local = primary_world_inv @ grip_r_world
    if grip_left_offset_m is not None:
        grip_l_local = Vector(grip_left_offset_m)
        anchor_modes["grip_left_mode"] = "explicit"
    else:
        grip_l_local = primary_world_inv @ grip_l_world
    if sight_offset_m is not None:
        sight_local = Vector(sight_offset_m)
        anchor_modes["sight_mode"] = "explicit"
    else:
        sight_local = primary_world_inv @ sight_world

    forward_local = muzzle_local - sight_local
    if forward_local.length < 1e-5:
        forward_local = Vector((0.0, 1.0, 0.0))
    else:
        forward_local.normalize()
    ads_target_local = sight_local + (forward_local * max(0.5, float(ads_aim_distance_m)))

    muzzle = _ensure_weapon_empty(
        name=muzzle_name,
        location=primary_world @ muzzle_local,
        imported_objects=valid_imported,
    )
    grip_r = _ensure_weapon_grip_empty(
        name="grip_r",
        location=primary_world @ grip_r_local,
        imported_objects=valid_imported,
    )
    grip_l = _ensure_weapon_grip_empty(
        name="grip_l",
        location=primary_world @ grip_l_local,
        imported_objects=valid_imported,
    )
    sight_anchor = _ensure_weapon_empty(
        name="sight_anchor",
        location=primary_world @ sight_local,
        imported_objects=valid_imported,
    )
    ads_target = _ensure_weapon_empty(
        name="ads_target",
        location=primary_world @ ads_target_local,
        imported_objects=valid_imported,
    )

    muzzle.parent = primary_weapon_mesh
    muzzle.matrix_parent_inverse = primary_weapon_mesh.matrix_world.inverted()
    grip_r.parent = primary_weapon_mesh
    grip_r.matrix_parent_inverse = primary_weapon_mesh.matrix_world.inverted()
    grip_l.parent = primary_weapon_mesh
    grip_l.matrix_parent_inverse = primary_weapon_mesh.matrix_world.inverted()
    sight_anchor.parent = primary_weapon_mesh
    sight_anchor.matrix_parent_inverse = primary_weapon_mesh.matrix_world.inverted()
    # Keep ADS target world-anchored to avoid dependency cycles when upper-body
    # aim constraints are baked into the same armature driving the weapon.
    ads_target.parent = None
    return weapon_meshes, muzzle, grip_r, grip_l, sight_anchor, ads_target, anchor_modes, reasons


def _render_thumbnail(OPS, objects: list[bpy.types.Object], out_path: Path, size: int = 256) -> float:
    t0 = time.perf_counter()
    OPS.setup_camera_and_light(objects, (55.0, 0.0, 35.0), 4.0)

    bpy.context.scene.render.engine = "BLENDER_EEVEE"
    bpy.context.scene.render.resolution_x = int(size)
    bpy.context.scene.render.resolution_y = int(size)
    bpy.context.scene.render.image_settings.file_format = "PNG"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    bpy.context.scene.render.filepath = str(out_path)
    bpy.ops.render.render(write_still=True)
    return time.perf_counter() - t0


def _export_glb(out_path: Path, apply_transforms: bool) -> float:
    t0 = time.perf_counter()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action="DESELECT")
    _prune_default_nla_tracks()
    for obj in bpy.data.objects:
        if obj.type == "ARMATURE":
            _disable_nla_evaluation(obj)
    bpy.ops.export_scene.gltf(
        filepath=str(out_path),
        export_format="GLB",
        use_selection=False,
        export_apply=bool(apply_transforms),
        export_yup=True,
        export_texcoords=True,
        export_normals=True,
        export_materials="EXPORT",
        export_animations=True,
        export_animation_mode="ACTIONS",
        export_nla_strips=False,
        export_merge_animation="ACTION",
        export_anim_single_armature=False,
    )
    return time.perf_counter() - t0


def _export_glb_selected(
    out_path: Path,
    *,
    apply_transforms: bool,
    selected_objects: list[bpy.types.Object],
) -> float:
    t0 = time.perf_counter()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action="DESELECT")
    _prune_default_nla_tracks()
    for obj in bpy.data.objects:
        if obj.type == "ARMATURE":
            _disable_nla_evaluation(obj)
    valid = [obj for obj in selected_objects if _object_exists(obj)]
    if not valid:
        raise RuntimeError("No selected objects available for export")
    for obj in valid:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = valid[0]
    bpy.ops.export_scene.gltf(
        filepath=str(out_path),
        export_format="GLB",
        use_selection=True,
        export_apply=bool(apply_transforms),
        export_yup=True,
        export_texcoords=True,
        export_normals=True,
        export_materials="EXPORT",
        export_animations=True,
        export_animation_mode="ACTIONS",
        export_nla_strips=False,
        export_merge_animation="ACTION",
        export_anim_single_armature=False,
    )
    return time.perf_counter() - t0


def _save_blend(blend_path: Path) -> float:
    t0 = time.perf_counter()
    blend_path.parent.mkdir(parents=True, exist_ok=True)
    _prune_default_nla_tracks()
    for obj in bpy.data.objects:
        if obj.type == "ARMATURE":
            _disable_nla_evaluation(obj)
            _clear_armature_nla_tracks(obj)
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
    return time.perf_counter() - t0


def _import_new_objects(glb_path: Path) -> list[bpy.types.Object]:
    before = set(bpy.data.objects.keys())
    bpy.ops.import_scene.gltf(filepath=str(glb_path))
    return [obj for name, obj in bpy.data.objects.items() if name not in before]


def _first_armature(objects: list[bpy.types.Object]) -> bpy.types.Object | None:
    for obj in objects:
        if obj.type == "ARMATURE":
            return obj
    return None


def _collect_actions_from_armature(armature_obj: bpy.types.Object) -> list[bpy.types.Action]:
    actions: list[bpy.types.Action] = []
    ad = getattr(armature_obj, "animation_data", None)
    if ad is None:
        return actions

    if ad.action is not None:
        actions.append(ad.action)
    for track in ad.nla_tracks:
        for strip in track.strips:
            if strip.action is not None:
                actions.append(strip.action)

    deduped: list[bpy.types.Action] = []
    seen: set[str] = set()
    for action in actions:
        if action.name in seen:
            continue
        seen.add(action.name)
        deduped.append(action)
    return deduped


def _collect_scene_actions() -> list[bpy.types.Action]:
    actions: list[bpy.types.Action] = []
    seen: set[str] = set()
    for action in bpy.data.actions:
        if action is None or action.name in seen:
            continue
        seen.add(action.name)
        actions.append(action)
    return actions


def _load_retarget_map(path: str | None) -> dict:
    if not path:
        return {}
    resolved = Path(path)
    if not resolved.exists():
        return {}
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _normalize_bone_map(payload: dict) -> dict[str, str]:
    raw = payload.get("bone_map")
    if not isinstance(raw, dict):
        raw = payload.get("source_to_target", {})
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        if key.strip() and value.strip():
            normalized[key.strip()] = value.strip()
    return normalized


def _resolve_target_bone_name(
    *,
    source_bone: str,
    target_armature: bpy.types.Object,
    bone_map: dict[str, str],
) -> str | None:
    target_bones = target_armature.data.bones
    if source_bone in target_bones:
        return source_bone

    if source_bone in bone_map and bone_map[source_bone] in target_bones:
        return bone_map[source_bone]

    lower_map = {k.lower(): v for k, v in bone_map.items()}
    mapped = lower_map.get(source_bone.lower())
    if mapped and mapped in target_bones:
        return mapped

    lower_targets = {bone.name.lower(): bone.name for bone in target_bones}
    if source_bone.lower() in lower_targets:
        return lower_targets[source_bone.lower()]
    return None


def _iter_action_fcurves(action: bpy.types.Action) -> list[tuple[object, bpy.types.FCurve]]:
    out: list[tuple[object, bpy.types.FCurve]] = []
    seen: set[tuple[int, int]] = set()

    legacy = getattr(action, "fcurves", None)
    if legacy is not None:
        for fcurve in legacy:
            key = (id(legacy), id(fcurve))
            if key in seen:
                continue
            seen.add(key)
            out.append((legacy, fcurve))

    layers = getattr(action, "layers", None)
    if layers:
        for layer in layers:
            strips = getattr(layer, "strips", None)
            if strips is None:
                continue
            for strip in strips:
                channelbags = getattr(strip, "channelbags", None)
                if channelbags is None:
                    continue
                for channelbag in channelbags:
                    layered = getattr(channelbag, "fcurves", None)
                    if layered is None:
                        continue
                    for fcurve in layered:
                        key = (id(layered), id(fcurve))
                        if key in seen:
                            continue
                        seen.add(key)
                        out.append((layered, fcurve))
    return out


def _retarget_action_to_armature(
    *,
    action: bpy.types.Action,
    target_armature: bpy.types.Object,
    bone_map: dict[str, str],
) -> bpy.types.Action:
    retargeted = action.copy()
    removable_by_owner: dict[int, tuple[object, list[bpy.types.FCurve]]] = {}
    for owner, fcurve in _iter_action_fcurves(retargeted):
        data_path = str(fcurve.data_path or "")
        if not data_path.startswith('pose.bones["'):
            continue
        try:
            source_bone = data_path.split('"')[1]
        except Exception:
            owner_key = id(owner)
            if owner_key not in removable_by_owner:
                removable_by_owner[owner_key] = (owner, [])
            removable_by_owner[owner_key][1].append(fcurve)
            continue
        target_bone = _resolve_target_bone_name(
            source_bone=source_bone,
            target_armature=target_armature,
            bone_map=bone_map,
        )
        if target_bone is None:
            owner_key = id(owner)
            if owner_key not in removable_by_owner:
                removable_by_owner[owner_key] = (owner, [])
            removable_by_owner[owner_key][1].append(fcurve)
            continue
        fcurve.data_path = data_path.replace(
            f'pose.bones["{source_bone}"]',
            f'pose.bones["{target_bone}"]',
            1,
        )
    for owner, removable in removable_by_owner.values():
        for fcurve in removable:
            owner.remove(fcurve)
    if hasattr(retargeted, "update_tag"):
        retargeted.update_tag()
    return retargeted


def _parse_anim_clip_entries(raw_entries: list[str]) -> list[tuple[str | None, Path]]:
    parsed: list[tuple[str | None, Path]] = []
    for raw in raw_entries:
        if not raw or "=" not in raw:
            continue
        clip_name, clip_path = raw.split("=", 1)
        normalized_name = clip_name.strip() or None
        normalized_path = clip_path.strip()
        if not normalized_path:
            continue
        parsed.append((normalized_name, Path(normalized_path)))
    return parsed


def _import_and_attach_animation_clips(
    *,
    base_armature: bpy.types.Object | None,
    anim_model_paths: list[str],
    anim_clip_entries: list[tuple[str | None, Path]],
    retarget_map_payload: dict,
) -> tuple[list[str], list[str], list[str]]:
    if base_armature is None:
        return [], ["no base armature available for animation attachment"], []
    if not anim_model_paths and not anim_clip_entries:
        return [], [], []

    clip_names: list[str] = []
    errors: list[str] = []
    retargeted_clips: list[str] = []
    bone_map = _normalize_bone_map(retarget_map_payload)

    if base_armature.animation_data is None:
        base_armature.animation_data_create()
    base_ad = base_armature.animation_data
    if base_ad is not None:
        base_ad.action = None
        for track in list(base_ad.nla_tracks):
            base_ad.nla_tracks.remove(track)
    _disable_nla_evaluation(base_armature)

    def _first_action_slot_handle(action: bpy.types.Action) -> int | None:
        slots = getattr(action, "slots", None)
        if not slots:
            return None
        try:
            return int(slots[0].handle)
        except Exception:
            return None

    clip_path_entries = list(anim_clip_entries) + [(None, Path(raw)) for raw in anim_model_paths]
    for requested_name, path in clip_path_entries:
        if not path.exists():
            errors.append(f"animation source missing: {path}")
            continue

        imported = _import_new_objects(path)
        src_arm = _first_armature(imported)
        if src_arm is None:
            errors.append(f"no armature in animation source: {path.name}")
            for obj in imported:
                if _object_exists(obj):
                    bpy.data.objects.remove(obj, do_unlink=True)
            continue

        source_actions = _collect_actions_from_armature(src_arm)
        if not source_actions:
            errors.append(f"no actions found in animation source: {path.name}")
            for obj in imported:
                if _object_exists(obj):
                    bpy.data.objects.remove(obj, do_unlink=True)
            continue

        base_name = requested_name or path.stem
        # Only force retarget when an explicit map is provided; otherwise preserve
        # original action curves, which are often already compatible.
        did_retarget = bool(bone_map) and (src_arm != base_armature)
        for idx, action in enumerate(source_actions):
            clip_name = base_name if idx == 0 else f"{base_name}_{idx+1}"
            if did_retarget:
                copied = _retarget_action_to_armature(
                    action=action,
                    target_armature=base_armature,
                    bone_map=bone_map,
                )
                retargeted_clips.append(clip_name)
            else:
                copied = action.copy()
            copied.name = clip_name
            # Keep clip actions alive in saved .blend files even after stripping
            # NLA tracks for deterministic single-action review.
            copied.use_fake_user = True
            if base_armature.animation_data is None:
                base_armature.animation_data_create()
            track = base_armature.animation_data.nla_tracks.new()
            track.name = clip_name
            start = int(max(1.0, float(copied.frame_range[0])))
            end = int(max(start + 1, float(copied.frame_range[1])))
            strip = track.strips.new(clip_name, start, copied)
            slot_handle = _first_action_slot_handle(copied)
            if slot_handle is not None and hasattr(strip, "action_slot_handle"):
                try:
                    strip.action_slot_handle = slot_handle
                except Exception:
                    pass
            # Blender 5 may create strips with zero effective influence; drive it explicitly.
            if hasattr(strip, "use_animated_influence"):
                strip.use_animated_influence = True
            strip.influence = 1.0
            try:
                strip.keyframe_insert(data_path="influence", frame=start)
                strip.keyframe_insert(data_path="influence", frame=end)
            except Exception:
                pass
            if base_armature.animation_data.action is None:
                base_armature.animation_data.action = copied
                if slot_handle is not None and hasattr(base_armature.animation_data, "action_slot_handle"):
                    try:
                        base_armature.animation_data.action_slot_handle = slot_handle
                    except Exception:
                        pass
            clip_names.append(clip_name)

        for obj in imported:
            if _object_exists(obj):
                bpy.data.objects.remove(obj, do_unlink=True)

    if clip_names and base_armature.animation_data and base_armature.animation_data.action is not None:
        action = base_armature.animation_data.action
        bpy.context.scene.frame_start = int(action.frame_range[0])
        bpy.context.scene.frame_end = int(action.frame_range[1])
        bpy.context.scene.frame_current = bpy.context.scene.frame_start

    return clip_names, errors, list(dict.fromkeys(retargeted_clips))


def _missing_requested_clips(requested_clips: list[str], exported_clips: list[str]) -> list[str]:
    if not requested_clips:
        return []
    exported_keys = {name.strip().lower() for name in exported_clips if name and name.strip()}
    missing: list[str] = []
    for name in requested_clips:
        key = name.strip().lower()
        if key and key not in exported_keys:
            missing.append(name)
    return missing


def _set_review_preview_pose(
    *,
    armature_obj: bpy.types.Object | None,
    preferred_clip_names: list[str],
) -> tuple[str, int]:
    if armature_obj is None or armature_obj.type != "ARMATURE":
        return "", 0
    ad = armature_obj.animation_data
    if ad is None:
        return "", 0

    candidates: list[str] = []
    for name in preferred_clip_names:
        clean = str(name).strip()
        if clean and clean not in candidates:
            candidates.append(clean)

    lower_to_action = {a.name.lower(): a for a in bpy.data.actions}
    action = None
    for clip_name in candidates:
        action = lower_to_action.get(clip_name.lower())
        if action is not None:
            break
    if action is None and ad.action is not None:
        action = ad.action
    if action is None:
        return "", 0

    ad.action = action
    slots = getattr(action, "slots", None)
    if slots and hasattr(ad, "action_slot_handle"):
        try:
            ad.action_slot_handle = int(slots[0].handle)
        except Exception:
            pass

    frame_start = int(max(1.0, float(action.frame_range[0])))
    bpy.context.scene.frame_current = frame_start
    bpy.context.scene.frame_set(frame_start)
    return action.name, frame_start


def _find_left_hand_bone_name(armature_obj: bpy.types.Object) -> str | None:
    if armature_obj.type != "ARMATURE" or armature_obj.data is None:
        return None
    bones = list(armature_obj.data.bones)
    if not bones:
        return None

    names = {b.name.lower(): b.name for b in bones}
    exact = [
        "mixamorig:lefthand",
        "lefthand",
        "hand.l",
        "left_hand",
        "bip001 l hand",
        "bip_l_hand",
    ]
    for key in exact:
        if key in names:
            return names[key]

    for bone in bones:
        n = bone.name.lower()
        if "hand" in n and (
            "left" in n
            or ".l" in n
            or "_l" in n
            or n.startswith("l_")
            or n.startswith("lhand")
            or n.startswith("l-hand")
            or n.endswith("_left")
            or n.endswith(".left")
        ):
            return bone.name
    return None


def _resolve_hand_bone_names(armature_obj: bpy.types.Object, retarget_map_payload: dict) -> tuple[str | None, str | None]:
    hand_r = retarget_map_payload.get("hand_r_bone")
    hand_l = retarget_map_payload.get("hand_l_bone")
    if not isinstance(hand_r, str) or not hand_r.strip():
        hand_r = _find_right_hand_bone_name(armature_obj)
    if not isinstance(hand_l, str) or not hand_l.strip():
        hand_l = _find_left_hand_bone_name(armature_obj)
    if isinstance(hand_r, str) and hand_r not in armature_obj.pose.bones:
        hand_r = _find_right_hand_bone_name(armature_obj)
    if isinstance(hand_l, str) and hand_l not in armature_obj.pose.bones:
        hand_l = _find_left_hand_bone_name(armature_obj)
    return hand_r, hand_l


def _resolve_left_arm_ik_owner_name(armature_obj: bpy.types.Object, hand_bone_name: str | None) -> str | None:
    if armature_obj.type != "ARMATURE":
        return None
    if hand_bone_name and hand_bone_name in armature_obj.pose.bones:
        hand_bone = armature_obj.pose.bones.get(hand_bone_name)
        if hand_bone is not None and hand_bone.parent is not None:
            return hand_bone.parent.name
    resolved = _find_bone_by_alias(
        armature_obj,
        exact=[
            "l_forearm",
            "forearm.l",
            "left_forearm",
            "leftforearm",
            "mixamorig:leftforearm",
            "lowerarm_l",
            "lowerarm.l",
        ],
        must_contain=["forearm"],
    )
    if resolved:
        return resolved
    return _find_bone_by_alias(
        armature_obj,
        exact=["l_upperarm", "upperarm.l", "left_upperarm", "leftupperarm"],
        must_contain=["upperarm"],
    )


def _resolve_right_arm_ik_owner_name(armature_obj: bpy.types.Object, hand_bone_name: str | None) -> str | None:
    if armature_obj.type != "ARMATURE":
        return None
    if hand_bone_name and hand_bone_name in armature_obj.pose.bones:
        hand_bone = armature_obj.pose.bones.get(hand_bone_name)
        if hand_bone is not None and hand_bone.parent is not None:
            return hand_bone.parent.name
    resolved = _find_bone_by_alias(
        armature_obj,
        exact=[
            "r_forearm",
            "forearm.r",
            "right_forearm",
            "rightforearm",
            "mixamorig:rightforearm",
            "lowerarm_r",
            "lowerarm.r",
        ],
        must_contain=["forearm"],
    )
    if resolved:
        return resolved
    return _find_bone_by_alias(
        armature_obj,
        exact=["r_upperarm", "upperarm.r", "right_upperarm", "rightupperarm"],
        must_contain=["upperarm"],
    )


def _ik_chain_count_for_owner(owner_bone: bpy.types.PoseBone, max_chain: int = 3) -> int:
    count = 1
    cursor = owner_bone.parent
    while cursor is not None and count < max_chain:
        count += 1
        lower = cursor.name.lower()
        if "clav" in lower or "shoulder" in lower or "spine" in lower:
            break
        cursor = cursor.parent
    return max(1, count)


def _ik_chain_bone_names(owner_bone: bpy.types.PoseBone, chain_count: int) -> list[str]:
    if chain_count <= 0:
        chain_count = 1
    names: list[str] = []
    cursor = owner_bone
    while cursor is not None and len(names) < chain_count:
        names.append(cursor.name)
        cursor = cursor.parent
    return names


def _apply_t_pose_offsets_from_map(armature_obj: bpy.types.Object, retarget_map_payload: dict) -> int:
    offsets = retarget_map_payload.get("t_pose_offsets_deg")
    if not isinstance(offsets, dict):
        return 0
    touched = 0
    for bone_name, rot_deg in offsets.items():
        if not isinstance(bone_name, str) or bone_name not in armature_obj.pose.bones:
            continue
        if not isinstance(rot_deg, (list, tuple)) or len(rot_deg) != 3:
            continue
        pbone = armature_obj.pose.bones[bone_name]
        pbone.rotation_mode = "XYZ"
        pbone.rotation_euler = tuple(math.radians(float(v)) for v in rot_deg)
        touched += 1
    return touched


def _measure_hand_lock_error_cm(
    armature_obj: bpy.types.Object,
    hand_bone_name: str | None,
    grip_target: bpy.types.Object | None,
    frame_start: int,
    frame_end: int,
) -> float:
    if not hand_bone_name or grip_target is None or hand_bone_name not in armature_obj.pose.bones:
        return 0.0
    scene = bpy.context.scene
    pbone = armature_obj.pose.bones[hand_bone_name]
    max_dist = 0.0
    span = max(1, frame_end - frame_start)
    step = max(1, span // 20)
    for frame in range(frame_start, frame_end + 1, step):
        scene.frame_set(frame)
        hand_loc = (armature_obj.matrix_world @ pbone.matrix).translation
        target_loc = grip_target.matrix_world.translation
        max_dist = max(max_dist, (hand_loc - target_loc).length)
    return max_dist * 100.0


def _bake_hand_locks_for_actions(
    *,
    armature_obj: bpy.types.Object | None,
    clip_names: list[str],
    grip_r: bpy.types.Object | None,
    grip_l: bpy.types.Object | None,
    retarget_map_payload: dict,
) -> tuple[list[str], float]:
    if armature_obj is None or armature_obj.type != "ARMATURE" or not clip_names:
        return [], 0.0
    if grip_r is None and grip_l is None:
        return [], 0.0

    hand_r, hand_l = _resolve_hand_bone_names(armature_obj, retarget_map_payload)
    if hand_r is None and hand_l is None:
        return [], 0.0

    baked_actions: list[str] = []
    max_error_cm = 0.0
    if armature_obj.animation_data is None:
        armature_obj.animation_data_create()

    for clip_name in clip_names:
        action = bpy.data.actions.get(clip_name)
        if action is None:
            continue

        bpy.ops.object.select_all(action="DESELECT")
        armature_obj.select_set(True)
        bpy.context.view_layer.objects.active = armature_obj
        bpy.ops.object.mode_set(mode="POSE")
        armature_obj.animation_data.action = action

        created_constraints: list[bpy.types.Constraint] = []
        if hand_r and grip_r is not None and hand_r in armature_obj.pose.bones:
            c = armature_obj.pose.bones[hand_r].constraints.new(type="COPY_LOCATION")
            c.name = f"hand_lock_{clip_name}_r"
            c.target = grip_r
            created_constraints.append(c)
        if hand_l and grip_l is not None and hand_l in armature_obj.pose.bones:
            c = armature_obj.pose.bones[hand_l].constraints.new(type="COPY_LOCATION")
            c.name = f"hand_lock_{clip_name}_l"
            c.target = grip_l
            created_constraints.append(c)
        if not created_constraints:
            bpy.ops.object.mode_set(mode="OBJECT")
            continue

        frame_start = int(max(1.0, float(action.frame_range[0])))
        frame_end = int(max(frame_start + 1, float(action.frame_range[1])))
        bpy.context.scene.frame_start = frame_start
        bpy.context.scene.frame_end = frame_end
        bpy.context.scene.frame_current = frame_start

        bpy.ops.nla.bake(
            frame_start=frame_start,
            frame_end=frame_end,
            step=1,
            only_selected=False,
            visual_keying=True,
            clear_constraints=True,
            use_current_action=True,
            clean_curves=False,
            bake_types={"POSE"},
        )
        bpy.ops.object.mode_set(mode="OBJECT")
        baked_actions.append(clip_name)

        err_r = _measure_hand_lock_error_cm(armature_obj, hand_r, grip_r, frame_start, frame_end)
        err_l = _measure_hand_lock_error_cm(armature_obj, hand_l, grip_l, frame_start, frame_end)
        max_error_cm = max(max_error_cm, err_r, err_l)

    return list(dict.fromkeys(baked_actions)), max_error_cm


def _is_ads_rifle_locomotion_clip(clip_name: str) -> bool:
    clip = clip_name.strip().lower()
    if "rifle" not in clip:
        return False
    return any(token in clip for token in ("idle", "walk", "run", "strafe"))


def _resolve_ads_clip_targets(
    *,
    ads_enabled: bool,
    requested_ads_clips: list[str],
    requested_clips: list[str],
    exported_clips: list[str],
) -> list[str]:
    if not ads_enabled:
        return []
    source = [name for name in requested_ads_clips if name.strip()]
    if not source:
        source = [name for name in requested_clips if _is_ads_rifle_locomotion_clip(name)]
    if not source:
        source = [name for name in exported_clips if _is_ads_rifle_locomotion_clip(name)]
    deduped: list[str] = []
    seen: set[str] = set()
    for name in source:
        key = name.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(name.strip())
    return deduped


def _resolve_pose_bone_name(armature_obj: bpy.types.Object, raw_name: str | None) -> str | None:
    if not raw_name or armature_obj.type != "ARMATURE":
        return None
    text = raw_name.strip()
    if not text:
        return None
    if text in armature_obj.pose.bones:
        return text
    by_lower = {bone.name.lower(): bone.name for bone in armature_obj.pose.bones}
    return by_lower.get(text.lower())


def _resolve_ads_head_bone_name(armature_obj: bpy.types.Object, explicit_name: str | None) -> str | None:
    resolved = _resolve_pose_bone_name(armature_obj, explicit_name)
    if resolved:
        return resolved
    return _find_bone_by_alias(
        armature_obj,
        exact=["head", "mixamorig:head", "head_top", "head_end"],
        must_contain=["head"],
    )


def _resolve_ads_spine_bones(armature_obj: bpy.types.Object, explicit_names: list[str]) -> list[str]:
    if armature_obj.type != "ARMATURE":
        return []
    resolved: list[str] = []
    seen: set[str] = set()
    for raw_name in explicit_names:
        name = _resolve_pose_bone_name(armature_obj, raw_name)
        if name is None:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        resolved.append(name)
    if resolved:
        return resolved

    preferred = [
        "spine01",
        "spine1",
        "spine_01",
        "spine02",
        "spine2",
        "spine_02",
        "necktwist01",
        "necktwist02",
        "neck01",
        "neck",
    ]
    by_lower = {bone.name.lower(): bone.name for bone in armature_obj.pose.bones}
    for key in preferred:
        bone_name = by_lower.get(key)
        if bone_name is None:
            continue
        lower = bone_name.lower()
        if lower in seen:
            continue
        seen.add(lower)
        resolved.append(bone_name)

    if resolved:
        return resolved

    for bone in armature_obj.pose.bones:
        lower = bone.name.lower()
        if "spine" in lower or "neck" in lower:
            if lower in seen:
                continue
            seen.add(lower)
            resolved.append(bone.name)
    return resolved


def _resolve_right_clavicle_bone_name(armature_obj: bpy.types.Object) -> str | None:
    return _find_bone_by_alias(
        armature_obj,
        exact=[
            "r_clavicle",
            "clavicle.r",
            "right_clavicle",
            "mixamorig:rightshoulder",
            "rightshoulder",
        ],
        must_contain=["clav"],
    ) or _find_bone_by_alias(
        armature_obj,
        exact=["shoulder.r", "r_shoulder", "right_shoulder"],
        must_contain=["shoulder"],
    )


def _bone_is_ancestor(
    armature_obj: bpy.types.Object,
    *,
    ancestor_bone_name: str,
    child_bone_name: str,
) -> bool:
    if armature_obj.type != "ARMATURE":
        return False
    child_bone = armature_obj.data.bones.get(child_bone_name)
    while child_bone is not None and child_bone.parent is not None:
        if child_bone.parent.name == ancestor_bone_name:
            return True
        child_bone = child_bone.parent
    return False


def _set_pose_bone_selected(
    armature_obj: bpy.types.Object,
    bone_name: str,
    *,
    selected: bool,
) -> None:
    if armature_obj.type != "ARMATURE":
        return
    pose_bone = armature_obj.pose.bones.get(bone_name)
    if pose_bone is not None and hasattr(pose_bone, "select"):
        try:
            pose_bone.select = bool(selected)
        except Exception:
            pass

    data_bone = armature_obj.data.bones.get(bone_name)
    if data_bone is None:
        return
    if hasattr(data_bone, "select"):
        try:
            data_bone.select = bool(selected)
        except Exception:
            pass
    if hasattr(data_bone, "select_head"):
        try:
            data_bone.select_head = bool(selected)
        except Exception:
            pass
    if hasattr(data_bone, "select_tail"):
        try:
            data_bone.select_tail = bool(selected)
        except Exception:
            pass
    if selected and hasattr(armature_obj.data.bones, "active"):
        try:
            armature_obj.data.bones.active = data_bone
        except Exception:
            pass


_TRACK_AXIS_LOCAL_VECTOR = {
    "TRACK_X": Vector((1.0, 0.0, 0.0)),
    "TRACK_Y": Vector((0.0, 1.0, 0.0)),
    "TRACK_Z": Vector((0.0, 0.0, 1.0)),
    "TRACK_NEGATIVE_X": Vector((-1.0, 0.0, 0.0)),
    "TRACK_NEGATIVE_Y": Vector((0.0, -1.0, 0.0)),
    "TRACK_NEGATIVE_Z": Vector((0.0, 0.0, -1.0)),
}


def _pose_bone_world_axis(
    armature_obj: bpy.types.Object,
    pose_bone: bpy.types.PoseBone,
    track_axis: str,
) -> Vector:
    local_axis = _TRACK_AXIS_LOCAL_VECTOR.get(track_axis, Vector((0.0, 1.0, 0.0)))
    world_axis = (armature_obj.matrix_world @ pose_bone.matrix).to_3x3() @ local_axis
    if world_axis.length < 1e-8:
        return Vector((0.0, 1.0, 0.0))
    world_axis.normalize()
    return world_axis


def _best_track_axis_for_target(
    armature_obj: bpy.types.Object,
    pose_bone: bpy.types.PoseBone,
    target_obj: bpy.types.Object,
) -> str:
    bone_loc = (armature_obj.matrix_world @ pose_bone.matrix).translation
    to_target = target_obj.matrix_world.translation - bone_loc
    if to_target.length < 1e-8:
        return "TRACK_Y"
    to_target.normalize()

    best_axis = "TRACK_Y"
    best_error = 180.0
    for axis in _TRACK_AXIS_LOCAL_VECTOR:
        axis_vec = _pose_bone_world_axis(armature_obj, pose_bone, axis)
        angle_deg = math.degrees(axis_vec.angle(to_target))
        if angle_deg < best_error:
            best_error = angle_deg
            best_axis = axis
    return best_axis


def _measure_head_aim_error_deg(
    armature_obj: bpy.types.Object,
    head_bone_name: str | None,
    aim_target: bpy.types.Object | None,
    frame_start: int,
    frame_end: int,
    track_axis: str,
) -> float:
    if (
        aim_target is None
        or not head_bone_name
        or head_bone_name not in armature_obj.pose.bones
    ):
        return 0.0
    scene = bpy.context.scene
    head_bone = armature_obj.pose.bones[head_bone_name]
    max_error = 0.0
    span = max(1, frame_end - frame_start)
    step = max(1, span // 20)
    for frame in range(frame_start, frame_end + 1, step):
        scene.frame_set(frame)
        head_loc = (armature_obj.matrix_world @ head_bone.matrix).translation
        to_target = aim_target.matrix_world.translation - head_loc
        if to_target.length < 1e-8:
            continue
        to_target.normalize()
        forward = _pose_bone_world_axis(armature_obj, head_bone, track_axis)
        max_error = max(max_error, math.degrees(forward.angle(to_target)))
    return max_error


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


def _set_linear_keyframes(obj: bpy.types.Object, data_path: str) -> None:
    ad = getattr(obj, "animation_data", None)
    action = getattr(ad, "action", None) if ad is not None else None
    if action is None or not hasattr(action, "fcurves"):
        return
    for fcurve in action.fcurves:
        if fcurve.data_path != data_path:
            continue
        for point in fcurve.keyframe_points:
            point.interpolation = "LINEAR"


def _safe_angle_deg(vec_a: Vector, vec_b: Vector) -> float:
    if vec_a.length < 1e-8 or vec_b.length < 1e-8:
        return 180.0
    return math.degrees(vec_a.normalized().angle(vec_b.normalized()))


def _measure_ads_clip_metrics(
    *,
    armature_obj: bpy.types.Object,
    hand_l_bone_name: str | None,
    hand_r_bone_name: str | None,
    head_bone_name: str | None,
    grip_l: bpy.types.Object | None,
    grip_r: bpy.types.Object | None,
    sight_anchor: bpy.types.Object | None,
    muzzle: bpy.types.Object | None,
    frame_start: int,
    frame_end: int,
    eye_offset_world: Vector,
    clip_name: str,
) -> dict:
    scene = bpy.context.scene
    sample_frames = _ads_sample_frames(frame_start, frame_end)

    left_grip_err_cm_max = 0.0
    right_grip_err_cm_max = 0.0
    eye_to_sight_m_max = 0.0
    alignment_deg_max = 0.0

    for frame in sample_frames:
        scene.frame_set(frame)

        if hand_l_bone_name and grip_l is not None and hand_l_bone_name in armature_obj.pose.bones:
            hand_l_world = (armature_obj.matrix_world @ armature_obj.pose.bones[hand_l_bone_name].matrix).translation
            left_grip_err_cm_max = max(left_grip_err_cm_max, (hand_l_world - grip_l.matrix_world.translation).length * 100.0)

        if hand_r_bone_name and grip_r is not None and hand_r_bone_name in armature_obj.pose.bones:
            hand_r_world = (armature_obj.matrix_world @ armature_obj.pose.bones[hand_r_bone_name].matrix).translation
            right_grip_err_cm_max = max(
                right_grip_err_cm_max,
                (hand_r_world - grip_r.matrix_world.translation).length * 100.0,
            )

        if (
            head_bone_name
            and head_bone_name in armature_obj.pose.bones
            and sight_anchor is not None
            and muzzle is not None
        ):
            head_world = (armature_obj.matrix_world @ armature_obj.pose.bones[head_bone_name].matrix).translation
            eye_world = head_world + eye_offset_world
            sight_world = sight_anchor.matrix_world.translation
            muzzle_world = muzzle.matrix_world.translation
            eye_to_sight = sight_world - eye_world
            eye_to_sight_m_max = max(eye_to_sight_m_max, eye_to_sight.length)
            weapon_forward = muzzle_world - sight_world
            alignment_deg_max = max(alignment_deg_max, _safe_angle_deg(weapon_forward, eye_to_sight))

    return {
        "clip": clip_name,
        "eye_to_sight_m_max": eye_to_sight_m_max,
        "alignment_deg_max": alignment_deg_max,
        "left_grip_err_cm_max": left_grip_err_cm_max,
        "right_grip_err_cm_max": right_grip_err_cm_max,
        "sampled_frames": sample_frames,
    }


def _bake_ads_for_actions(
    *,
    armature_obj: bpy.types.Object | None,
    clip_names: list[str],
    grip_r: bpy.types.Object | None,
    grip_l: bpy.types.Object | None,
    sight_anchor: bpy.types.Object | None,
    muzzle: bpy.types.Object | None,
    retarget_map_payload: dict,
    ads_head_bone: str | None,
    ads_spine_bones: list[str],
    ads_eye_offset_world: tuple[float, float, float] | None,
) -> dict:
    result = {
        "baked_actions": [],
        "left_hand_grip_error_cm_max": 0.0,
        "right_hand_grip_error_cm_max": 0.0,
        "head_aim_error_deg_max": 0.0,
        "ads_eye_to_sight_m_max": 0.0,
        "ads_eye_weapon_alignment_deg_max": 0.0,
        "ads_clip_metrics": [],
        "ads_failures": [],
        "resolved_head_bone": "",
        "resolved_spine_bones": [],
    }
    if armature_obj is None or armature_obj.type != "ARMATURE" or not clip_names:
        return result

    _disable_nla_evaluation(armature_obj)

    hand_r, hand_l = _resolve_hand_bone_names(armature_obj, retarget_map_payload)
    head_name = _resolve_ads_head_bone_name(armature_obj, ads_head_bone)
    spine_chain = _resolve_ads_spine_bones(armature_obj, ads_spine_bones)
    right_clavicle_name = _resolve_right_clavicle_bone_name(armature_obj)
    if right_clavicle_name:
        spine_keys = {name.strip().lower() for name in spine_chain if name.strip()}
        if right_clavicle_name.strip().lower() not in spine_keys:
            spine_chain.append(right_clavicle_name)
    left_ik_owner_name = _resolve_left_arm_ik_owner_name(armature_obj, hand_l)
    right_ik_owner_name = _resolve_right_arm_ik_owner_name(armature_obj, hand_r)
    result["resolved_head_bone"] = head_name or ""
    result["resolved_spine_bones"] = spine_chain

    if hand_l is None:
        result["ads_failures"].append("left hand bone unresolved for ADS")
        return result
    if hand_r is None:
        result["ads_failures"].append("right hand bone unresolved for ADS")
        return result
    if left_ik_owner_name is None:
        result["ads_failures"].append("left arm IK owner unresolved for ADS")
        return result
    if right_ik_owner_name is None:
        result["ads_failures"].append("right arm IK owner unresolved for ADS")
        return result
    if grip_l is None or not _object_exists(grip_l):
        result["ads_failures"].append("left grip target missing for ADS")
        return result
    if grip_r is None or not _object_exists(grip_r):
        result["ads_failures"].append("right grip target missing for ADS")
        return result
    if sight_anchor is None or not _object_exists(sight_anchor):
        result["ads_failures"].append("sight anchor missing for ADS")
        return result
    if muzzle is None or not _object_exists(muzzle):
        result["ads_failures"].append("muzzle target missing for ADS")
        return result
    if head_name is None:
        result["ads_failures"].append("head bone unresolved for ADS")
        return result

    if armature_obj.animation_data is None:
        armature_obj.animation_data_create()

    baked_actions: list[str] = []
    left_error_max = 0.0
    right_error_max = 0.0
    eye_to_sight_max = 0.0
    alignment_max = 0.0
    clip_metrics: list[dict] = []
    scene = bpy.context.scene
    eye_offset_world = Vector(ads_eye_offset_world or (0.0, 0.0, 0.0))
    ads_max_grip_delta_m = 0.35

    for clip_name in clip_names:
        action = bpy.data.actions.get(clip_name)
        if action is None:
            continue

        bpy.ops.object.select_all(action="DESELECT")
        armature_obj.select_set(True)
        bpy.context.view_layer.objects.active = armature_obj
        bpy.ops.object.mode_set(mode="POSE")
        armature_obj.animation_data.action = action

        left_bone = armature_obj.pose.bones.get(hand_l)
        right_bone = armature_obj.pose.bones.get(hand_r)
        left_ik_owner = armature_obj.pose.bones.get(left_ik_owner_name)
        right_ik_owner = armature_obj.pose.bones.get(right_ik_owner_name)
        head_bone = armature_obj.pose.bones.get(head_name)
        if left_bone is None:
            result["ads_failures"].append(f"{clip_name}: left hand pose bone missing")
            bpy.ops.object.mode_set(mode="OBJECT")
            continue
        if right_bone is None:
            result["ads_failures"].append(f"{clip_name}: right hand pose bone missing")
            bpy.ops.object.mode_set(mode="OBJECT")
            continue
        if left_ik_owner is None:
            result["ads_failures"].append(f"{clip_name}: left IK owner pose bone missing")
            bpy.ops.object.mode_set(mode="OBJECT")
            continue
        if right_ik_owner is None:
            result["ads_failures"].append(f"{clip_name}: right IK owner pose bone missing")
            bpy.ops.object.mode_set(mode="OBJECT")
            continue
        if head_bone is None:
            result["ads_failures"].append(f"{clip_name}: head pose bone missing")
            bpy.ops.object.mode_set(mode="OBJECT")
            continue

        frame_start = int(max(1.0, float(action.frame_range[0])))
        frame_end = int(max(frame_start + 1, float(action.frame_range[1])))
        sample_frames = _ads_sample_frames(frame_start, frame_end)

        target_name = f"ads_grip_r_target_{clip_name}"
        target_obj = bpy.data.objects.get(target_name)
        if target_obj is not None and _object_exists(target_obj):
            bpy.data.objects.remove(target_obj, do_unlink=True)
        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.empty_add(type="PLAIN_AXES", location=(0.0, 0.0, 0.0))
        target_obj = bpy.context.active_object
        target_obj.name = target_name
        target_obj.rotation_mode = "QUATERNION"
        if target_obj.animation_data is not None and target_obj.animation_data.action is not None:
            old_action = target_obj.animation_data.action
            bpy.data.actions.remove(old_action)

        for frame in sample_frames:
            scene.frame_set(frame)
            head_world = (armature_obj.matrix_world @ head_bone.matrix).translation
            eye_world = head_world + eye_offset_world
            grip_r_world = grip_r.matrix_world.translation
            sight_world = sight_anchor.matrix_world.translation
            muzzle_world = muzzle.matrix_world.translation

            delta_world = sight_world - grip_r_world
            desired_grip_r_world = eye_world - delta_world
            displacement = desired_grip_r_world - grip_r_world
            if displacement.length > ads_max_grip_delta_m:
                displacement.normalize()
                desired_grip_r_world = grip_r_world + (displacement * ads_max_grip_delta_m)

            desired_forward = sight_world - eye_world
            current_forward = muzzle_world - sight_world
            hand_world_rot = (armature_obj.matrix_world @ right_bone.matrix).to_quaternion()
            desired_hand_rot = hand_world_rot
            if desired_forward.length > 1e-8 and current_forward.length > 1e-8:
                rot_delta = current_forward.normalized().rotation_difference(desired_forward.normalized())
                desired_hand_rot = rot_delta @ hand_world_rot

            target_obj.location = desired_grip_r_world
            target_obj.rotation_quaternion = desired_hand_rot
            target_obj.keyframe_insert(data_path="location", frame=frame)
            target_obj.keyframe_insert(data_path="rotation_quaternion", frame=frame)
        _set_linear_keyframes(target_obj, "location")
        _set_linear_keyframes(target_obj, "rotation_quaternion")

        bpy.ops.object.select_all(action="DESELECT")
        armature_obj.select_set(True)
        bpy.context.view_layer.objects.active = armature_obj
        bpy.ops.object.mode_set(mode="POSE")
        armature_obj.animation_data.action = action

        bpy.ops.pose.select_all(action="DESELECT")
        bones_to_bake: list[str] = []
        created_constraints: list[tuple[bpy.types.PoseBone, str]] = []

        def _add_bake_bone(bone_name: str) -> None:
            key = bone_name.strip()
            if not key:
                return
            if key not in armature_obj.pose.bones:
                return
            _set_pose_bone_selected(armature_obj, key, selected=True)
            if key not in bones_to_bake:
                bones_to_bake.append(key)

        _add_bake_bone(left_bone.name)
        _add_bake_bone(right_bone.name)

        left_chain_count = _ik_chain_count_for_owner(left_ik_owner, max_chain=3)
        right_chain_count = _ik_chain_count_for_owner(right_ik_owner, max_chain=5)

        ik_left = left_ik_owner.constraints.new(type="IK")
        ik_left.name = f"ads_lock_{clip_name}_ik_l"
        ik_left.target = grip_l
        ik_left.chain_count = left_chain_count
        if hasattr(ik_left, "use_stretch"):
            ik_left.use_stretch = False
        if hasattr(ik_left, "use_tail"):
            ik_left.use_tail = True
        ik_left.influence = 1.0
        created_constraints.append((left_ik_owner, ik_left.name))

        ik_right = right_ik_owner.constraints.new(type="IK")
        ik_right.name = f"ads_lock_{clip_name}_ik_r"
        ik_right.target = target_obj
        ik_right.chain_count = right_chain_count
        if hasattr(ik_right, "use_rotation"):
            ik_right.use_rotation = True
        if hasattr(ik_right, "use_stretch"):
            ik_right.use_stretch = False
        if hasattr(ik_right, "use_tail"):
            ik_right.use_tail = True
        ik_right.influence = 1.0
        created_constraints.append((right_ik_owner, ik_right.name))

        copy_rot = left_bone.constraints.new(type="COPY_ROTATION")
        copy_rot.name = f"ads_lock_rot_{clip_name}_l"
        copy_rot.target = grip_l
        copy_rot.influence = 0.1
        copy_rot.owner_space = "WORLD"
        copy_rot.target_space = "WORLD"
        created_constraints.append((left_bone, copy_rot.name))

        right_rot = right_bone.constraints.new(type="COPY_ROTATION")
        right_rot.name = f"ads_lock_rot_{clip_name}_r"
        right_rot.target = target_obj
        right_rot.influence = 1.0
        right_rot.owner_space = "WORLD"
        right_rot.target_space = "WORLD"
        created_constraints.append((right_bone, right_rot.name))

        for chain_name in _ik_chain_bone_names(left_ik_owner, left_chain_count):
            _add_bake_bone(chain_name)
        for chain_name in _ik_chain_bone_names(right_ik_owner, right_chain_count):
            _add_bake_bone(chain_name)

        left_clavicle_name = _find_bone_by_alias(
            armature_obj,
            exact=["l_clavicle", "clavicle.l", "left_clavicle", "mixamorig:leftshoulder", "leftshoulder"],
            must_contain=["clav"],
        ) or _find_bone_by_alias(
            armature_obj,
            exact=["shoulder.l", "l_shoulder", "left_shoulder"],
            must_contain=["shoulder"],
        )
        if left_clavicle_name:
            _add_bake_bone(left_clavicle_name)
        if right_clavicle_name:
            _add_bake_bone(right_clavicle_name)

        scene.frame_start = frame_start
        scene.frame_end = frame_end
        scene.frame_current = frame_start
        scene.frame_set(frame_start)
        bpy.context.view_layer.update()

        bake_result: set[str] = set()
        bake_ok = False
        try:
            raw = bpy.ops.nla.bake(
                frame_start=frame_start,
                frame_end=frame_end,
                step=1,
                only_selected=True,
                visual_keying=True,
                clear_constraints=True,
                use_current_action=True,
                clean_curves=False,
                bake_types={"POSE"},
            )
            if isinstance(raw, set):
                bake_result = {str(v) for v in raw}
            bake_ok = "FINISHED" in bake_result and "CANCELLED" not in bake_result
        except Exception as exc:  # noqa: BLE001
            result["ads_failures"].append(f"{clip_name}: ADS bake error ({exc})")
        finally:
            for pbone, constraint_name in created_constraints:
                existing = pbone.constraints.get(constraint_name)
                if existing is not None:
                    pbone.constraints.remove(existing)
            bpy.ops.object.mode_set(mode="OBJECT")
            if _object_exists(target_obj):
                bpy.data.objects.remove(target_obj, do_unlink=True)

        if not bake_ok:
            if not any(f"{clip_name}: ADS bake error" in msg for msg in result["ads_failures"]):
                state = ",".join(sorted(bake_result)) if bake_result else "no_result"
                result["ads_failures"].append(f"{clip_name}: ADS bake not applied ({state})")
            continue
        baked_actions.append(clip_name)

        armature_obj.animation_data.action = action
        metrics = _measure_ads_clip_metrics(
            armature_obj=armature_obj,
            hand_l_bone_name=hand_l,
            hand_r_bone_name=hand_r,
            head_bone_name=head_name,
            grip_l=grip_l,
            grip_r=grip_r,
            sight_anchor=sight_anchor,
            muzzle=muzzle,
            frame_start=frame_start,
            frame_end=frame_end,
            eye_offset_world=eye_offset_world,
            clip_name=clip_name,
        )
        clip_metrics.append(metrics)
        left_error_max = max(left_error_max, float(metrics.get("left_grip_err_cm_max", 0.0)))
        right_error_max = max(right_error_max, float(metrics.get("right_grip_err_cm_max", 0.0)))
        eye_to_sight_max = max(eye_to_sight_max, float(metrics.get("eye_to_sight_m_max", 0.0)))
        alignment_max = max(alignment_max, float(metrics.get("alignment_deg_max", 0.0)))

    unique_baked = list(dict.fromkeys(baked_actions))
    baked_keys = {name.strip().lower() for name in unique_baked if name.strip()}
    for clip_name in clip_names:
        key = clip_name.strip().lower()
        if key and key not in baked_keys:
            result["ads_failures"].append(f"{clip_name}: ADS bake missing")

    result["baked_actions"] = unique_baked
    result["left_hand_grip_error_cm_max"] = left_error_max
    result["right_hand_grip_error_cm_max"] = right_error_max
    result["head_aim_error_deg_max"] = alignment_max
    result["ads_eye_to_sight_m_max"] = eye_to_sight_max
    result["ads_eye_weapon_alignment_deg_max"] = alignment_max
    result["ads_clip_metrics"] = clip_metrics
    result["ads_failures"] = list(dict.fromkeys(str(v) for v in result["ads_failures"] if str(v).strip()))
    return result


def _find_bone_by_alias(
    armature_obj: bpy.types.Object,
    *,
    exact: list[str],
    must_contain: list[str],
) -> str | None:
    if armature_obj.type != "ARMATURE" or armature_obj.data is None:
        return None
    bones = list(armature_obj.data.bones)
    if not bones:
        return None
    by_lower = {bone.name.lower(): bone.name for bone in bones}
    for name in exact:
        if name in by_lower:
            return by_lower[name]
    for bone in bones:
        lower = bone.name.lower()
        if all(token in lower for token in must_contain):
            return bone.name
    return None


def _find_foot_bones(armature_obj: bpy.types.Object, retarget_map_payload: dict) -> tuple[str | None, str | None]:
    foot_l = retarget_map_payload.get("foot_l_bone")
    foot_r = retarget_map_payload.get("foot_r_bone")
    if not isinstance(foot_l, str) or foot_l not in armature_obj.pose.bones:
        foot_l = _find_bone_by_alias(
            armature_obj,
            exact=[
                "mixamorig:leftfoot",
                "leftfoot",
                "foot.l",
                "left_foot",
                "l_foot",
                "bip001 l foot",
            ],
            must_contain=["foot"],
        )
        if foot_l and ".r" in foot_l.lower():
            foot_l = None
    if not isinstance(foot_r, str) or foot_r not in armature_obj.pose.bones:
        foot_r = _find_bone_by_alias(
            armature_obj,
            exact=[
                "mixamorig:rightfoot",
                "rightfoot",
                "foot.r",
                "right_foot",
                "r_foot",
                "bip001 r foot",
            ],
            must_contain=["foot"],
        )
        if foot_r and ".l" in foot_r.lower():
            foot_r = None
    return foot_l, foot_r


def _find_motion_root_bone(armature_obj: bpy.types.Object, retarget_map_payload: dict) -> str | None:
    candidate = retarget_map_payload.get("root_bone")
    if isinstance(candidate, str) and candidate in armature_obj.pose.bones:
        return candidate
    for preferred in ["root", "hips", "pelvis", "mixamorig:hips"]:
        pose_bone = armature_obj.pose.bones.get(preferred)
        if pose_bone is not None:
            return pose_bone.name
    if armature_obj.pose.bones:
        return armature_obj.pose.bones[0].name
    return None


def _movement_kind(clip_name: str) -> str:
    name = clip_name.lower()
    if "strafe" in name:
        return "strafe"
    if "run" in name:
        return "run"
    if "walk" in name:
        return "walk"
    if "idle" in name:
        return "idle"
    return "other"


def _sample_frames(frame_start: int, frame_end: int, max_samples: int = 40) -> list[int]:
    if frame_end <= frame_start:
        return [frame_start, frame_end]
    step = max(1, (frame_end - frame_start) // max_samples)
    frames = list(range(frame_start, frame_end + 1, step))
    if frames[-1] != frame_end:
        frames.append(frame_end)
    return frames


def _span_and_loop_from_positions(positions: list[Vector]) -> tuple[float, float]:
    if not positions:
        return 0.0, 0.0
    origin = positions[0]
    span = 0.0
    for pos in positions:
        span = max(span, float((pos - origin).length))
    loop_error = float((positions[-1] - origin).length)
    return span, loop_error


def _collect_clip_motion_metrics(
    *,
    armature_obj: bpy.types.Object | None,
    clip_names: list[str],
    retarget_map_payload: dict,
) -> tuple[dict[str, dict], list[str]]:
    if armature_obj is None or armature_obj.type != "ARMATURE" or not clip_names:
        return {}, []

    ad = armature_obj.animation_data
    if ad is None:
        armature_obj.animation_data_create()
        ad = armature_obj.animation_data
    if ad is None:
        return {}, []

    scene = bpy.context.scene
    original_frame = int(scene.frame_current)
    original_action = ad.action
    original_use_nla = bool(getattr(ad, "use_nla", True))
    if hasattr(ad, "use_nla"):
        ad.use_nla = False

    root_bone = _find_motion_root_bone(armature_obj, retarget_map_payload)
    foot_l_bone, foot_r_bone = _find_foot_bones(armature_obj, retarget_map_payload)

    metrics: dict[str, dict] = {}
    failures: list[str] = []
    try:
        for clip_name in clip_names:
            action = bpy.data.actions.get(clip_name)
            if action is None:
                continue
            ad.action = action
            frame_start = int(max(1.0, float(action.frame_range[0])))
            frame_end = int(max(frame_start + 1, float(action.frame_range[1])))
            frames = _sample_frames(frame_start, frame_end)

            root_local_xy_max = 0.0
            root_local_xy_seam = 0.0
            foot_l_positions: list[Vector] = []
            foot_r_positions: list[Vector] = []

            root_pose = armature_obj.pose.bones.get(root_bone) if root_bone else None
            foot_l_pose = armature_obj.pose.bones.get(foot_l_bone) if foot_l_bone else None
            foot_r_pose = armature_obj.pose.bones.get(foot_r_bone) if foot_r_bone else None

            root_start_xy: tuple[float, float] | None = None
            root_end_xy: tuple[float, float] | None = None
            for frame in frames:
                scene.frame_set(frame)
                if root_pose is not None:
                    loc = root_pose.location
                    root_local_xy_max = max(root_local_xy_max, abs(float(loc.x)), abs(float(loc.y)))
                    if frame == frame_start:
                        root_start_xy = (float(loc.x), float(loc.y))
                    if frame == frame_end:
                        root_end_xy = (float(loc.x), float(loc.y))
                if foot_l_pose is not None:
                    foot_l_positions.append((armature_obj.matrix_world @ foot_l_pose.matrix).translation.copy())
                if foot_r_pose is not None:
                    foot_r_positions.append((armature_obj.matrix_world @ foot_r_pose.matrix).translation.copy())

            if root_start_xy is not None and root_end_xy is not None:
                dx = root_end_xy[0] - root_start_xy[0]
                dy = root_end_xy[1] - root_start_xy[1]
                root_local_xy_seam = math.sqrt(dx * dx + dy * dy)

            foot_l_span, foot_l_loop = _span_and_loop_from_positions(foot_l_positions)
            foot_r_span, foot_r_loop = _span_and_loop_from_positions(foot_r_positions)
            foot_span_avg = (foot_l_span + foot_r_span) * 0.5
            foot_loop_avg = (foot_l_loop + foot_r_loop) * 0.5

            metrics[clip_name] = {
                "movement_kind": _movement_kind(clip_name),
                "frame_start": frame_start,
                "frame_end": frame_end,
                "sample_count": len(frames),
                "root_bone": root_bone or "",
                "foot_l_bone": foot_l_bone or "",
                "foot_r_bone": foot_r_bone or "",
                "root_local_xy_max_m": root_local_xy_max,
                "root_local_xy_seam_m": root_local_xy_seam,
                "foot_l_span_m": foot_l_span,
                "foot_r_span_m": foot_r_span,
                "foot_span_avg_m": foot_span_avg,
                "foot_l_loop_m": foot_l_loop,
                "foot_r_loop_m": foot_r_loop,
                "foot_loop_avg_m": foot_loop_avg,
            }

        walk_span = None
        run_span = None
        for clip_name, clip_metric in metrics.items():
            kind = str(clip_metric.get("movement_kind", "other"))
            foot_span = float(clip_metric.get("foot_span_avg_m") or 0.0)
            if kind == "walk":
                walk_span = foot_span if walk_span is None else max(walk_span, foot_span)
                if foot_span < 0.08:
                    failures.append(f"{clip_name}: walk span too low ({foot_span:.3f}m)")
            elif kind == "run":
                run_span = foot_span if run_span is None else max(run_span, foot_span)
                if foot_span < 0.10:
                    failures.append(f"{clip_name}: run span too low ({foot_span:.3f}m)")
            elif kind == "strafe":
                if foot_span < 0.08:
                    failures.append(f"{clip_name}: strafe span too low ({foot_span:.3f}m)")
            elif kind == "idle":
                if foot_span > 0.30:
                    failures.append(f"{clip_name}: idle span too high ({foot_span:.3f}m)")

        if walk_span is not None and run_span is not None and run_span < walk_span * 1.05:
            failures.append(
                f"run locomotion not stronger than walk (run={run_span:.3f}m walk={walk_span:.3f}m)"
            )
    finally:
        scene.frame_set(original_frame)
        ad.action = original_action
        if hasattr(ad, "use_nla"):
            ad.use_nla = original_use_nla

    return metrics, list(dict.fromkeys(failures))


def _run_procedural(args: argparse.Namespace, OPS) -> dict:
    recipe_path = Path(args.recipe)
    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))

    timings: dict[str, float] = {}
    t0 = time.perf_counter()
    OPS.wipe_scene()
    timings["wipe_scene_s"] = time.perf_counter() - t0

    scene_t0 = time.perf_counter()
    project_root = Path.cwd()

    materials = {}
    for material_spec in recipe["materials"]:
        material = OPS.create_material(material_spec, project_root)
        materials[material_spec["name"]] = material

    created_objects = []
    for obj_spec in recipe["objects"]:
        obj = OPS.create_primitive(obj_spec["primitive"], obj_spec["name"])
        OPS.apply_transform(
            obj,
            tuple(obj_spec["location"]),
            tuple(obj_spec["rotation_deg"]),
            tuple(obj_spec["scale"]),
        )
        for op in obj_spec.get("ops", []):
            OPS.apply_op(obj, op)

        material_name = obj_spec["material"]
        if material_name not in materials:
            raise ValueError(f"Object references unknown material: {material_name}")
        OPS.assign_material(obj, materials[material_name])
        created_objects.append(obj)

    timings["build_meshes_s"] = time.perf_counter() - scene_t0

    export_cfg = recipe["export"]
    if export_cfg.get("apply_transforms", True):
        t_apply = time.perf_counter()
        OPS.apply_object_transforms(created_objects)
        timings["apply_transforms_s"] = time.perf_counter() - t_apply

    if export_cfg.get("uv_unwrap") == "smart":
        t_uv = time.perf_counter()
        for obj in created_objects:
            OPS.smart_uv_unwrap(obj)
        timings["uv_unwrap_s"] = time.perf_counter() - t_uv

    if export_cfg.get("triangulate", True):
        t_tri = time.perf_counter()
        for obj in created_objects:
            OPS.triangulate_object(obj)
        timings["triangulate_s"] = time.perf_counter() - t_tri

    t_budget = time.perf_counter()
    tris_before_budget = OPS.count_triangles(created_objects)
    tris_after_budget = OPS.enforce_target_tris(created_objects, int(export_cfg["target_tris"]))
    timings["enforce_budget_s"] = time.perf_counter() - t_budget

    timings["render_thumb_s"] = _render_thumbnail(OPS, created_objects, Path(args.thumb), size=256)
    timings["export_glb_s"] = _export_glb(Path(args.out), apply_transforms=bool(export_cfg.get("apply_transforms", True)))
    timings["save_blend_s"] = _save_blend(Path(args.blend))

    mins, maxs = OPS.bounds_min_max(created_objects)
    report = {
        "asset_name": recipe["name"],
        "mode": "procedural",
        "triangle_count": tris_after_budget,
        "triangle_count_before_budget": tris_before_budget,
        "target_tris": int(export_cfg["target_tris"]),
        "object_count": len(created_objects),
        "missing_uvs": OPS.missing_uv_objects(created_objects),
        "bbox_min": [mins.x, mins.y, mins.z],
        "bbox_max": [maxs.x, maxs.y, maxs.z],
        "height_m": OPS.model_height(created_objects),
        "material_count": len([m for m in bpy.data.materials if m.users > 0]),
        "texture_count": OPS.count_textures(),
        "missing_textures": [],
        "alpha_fixed": False,
        "backface_culling_disabled": False,
        "tris_lod0": tris_after_budget,
        "tris_lod1": tris_after_budget,
        "objects_before": len(created_objects),
        "objects_after": len(created_objects),
        "rig_present": OPS.rig_present(),
        "animations_present": OPS.animations_present(),
        "clip_names": [],
        "clip_count": 0,
        "export_path": str(Path(args.out)),
        "thumbnail_path": str(Path(args.thumb)),
        "blend_path": str(Path(args.blend)),
        "timings": timings,
        "status": "ok",
    }
    return report


def _run_weapon_normalize(args: argparse.Namespace, OPS) -> dict:
    input_model = Path(args.input_model)
    if args.target_length_m is None or args.target_length_m <= 0:
        raise ValueError("--target_length_m must be provided and > 0 in weapon mode")
    if args.target_tris is None or args.target_tris <= 0:
        raise ValueError("--target_tris must be provided and > 0 in weapon mode")

    timings: dict[str, float] = {}
    t0 = time.perf_counter()
    OPS.wipe_scene()
    timings["wipe_scene_s"] = time.perf_counter() - t0

    t_import = time.perf_counter()
    imported_all = _import_new_objects(input_model)
    weapon_meshes = [o for o in imported_all if o.type == "MESH"]
    timings["import_glb_s"] = time.perf_counter() - t_import
    if not weapon_meshes:
        raise RuntimeError(f"No mesh objects found after importing {input_model}")

    objects_before = len(weapon_meshes)
    weapon_meshes, joined = OPS.safe_join_objects(weapon_meshes)
    if not weapon_meshes:
        raise RuntimeError("Weapon join step produced no meshes")

    removed_objects: list[str] = OPS.delete_objects([o for o in imported_all if o.type == "ARMATURE"])

    t_apply = time.perf_counter()
    OPS.apply_object_transforms(weapon_meshes)
    timings["apply_transforms_s"] = time.perf_counter() - t_apply

    t_normals = time.perf_counter()
    for obj in weapon_meshes:
        OPS.recalc_normals(obj)
        OPS.op_shade_smooth(obj)
        OPS.enable_auto_smooth(obj)
        OPS.apply_weighted_normal(obj)
    timings["fix_normals_s"] = time.perf_counter() - t_normals

    primary_rgba = _color_to_rgba(args.primary_color, (0.3, 0.3, 0.3, 1.0))
    secondary_rgba = _color_to_rgba(args.secondary_color, (0.45, 0.3, 0.2, 1.0))

    t_materials = time.perf_counter()
    mat_stats = OPS.sanitize_character_materials(
        weapon_meshes,
        primary_rgba=primary_rgba,
        secondary_rgba=secondary_rgba,
        disable_backface_culling=False,
    )
    timings["sanitize_materials_s"] = time.perf_counter() - t_materials

    t_pack = time.perf_counter()
    OPS.pack_all_images()
    timings["pack_images_s"] = time.perf_counter() - t_pack

    t_scale = time.perf_counter()
    scale_factor = OPS.scale_objects_to_max_dimension(weapon_meshes, float(args.target_length_m))
    timings["scale_to_length_s"] = time.perf_counter() - t_scale

    t_ground = time.perf_counter()
    OPS.move_objects_to_ground_center(weapon_meshes)
    timings["ground_origin_s"] = time.perf_counter() - t_ground

    t_tri = time.perf_counter()
    for obj in weapon_meshes:
        OPS.triangulate_object(obj)
    timings["triangulate_s"] = time.perf_counter() - t_tri

    t_budget = time.perf_counter()
    tris_before = OPS.count_triangles(weapon_meshes)
    tris_after = OPS.enforce_target_tris(weapon_meshes, int(args.target_tris))
    timings["enforce_budget_s"] = time.perf_counter() - t_budget

    muzzle = _ensure_weapon_muzzle_empty(
        OPS=OPS,
        weapon_meshes=weapon_meshes,
        imported_objects=[o for o in imported_all if o.name in bpy.data.objects],
        muzzle_name=args.weapon_muzzle_socket_name,
    )

    export_objects: list[bpy.types.Object] = list(weapon_meshes)
    export_objects.append(muzzle)
    exported_object_names = [obj.name for obj in export_objects]

    timings["render_thumb_s"] = _render_thumbnail(OPS, weapon_meshes, Path(args.thumb), size=256)
    timings["export_glb_s"] = _export_glb_selected(Path(args.out), apply_transforms=True, selected_objects=export_objects)
    timings["save_blend_s"] = _save_blend(Path(args.blend))

    mins, maxs = OPS.bounds_min_max(weapon_meshes)
    dims = OPS.bounds_dimensions(weapon_meshes)
    report = {
        "asset_name": input_model.stem,
        "mode": "weapon",
        "status": "ok",
        "reasons": [],
        "triangle_count": tris_after,
        "triangle_count_before_budget": tris_before,
        "target_tris": int(args.target_tris),
        "tris_lod0": tris_after,
        "tris_lod1": tris_after,
        "objects_before": objects_before,
        "objects_after": len(weapon_meshes),
        "object_count": len(weapon_meshes),
        "joined_meshes": joined,
        "missing_uvs": OPS.missing_uv_objects(weapon_meshes),
        "bbox_min": [mins.x, mins.y, mins.z],
        "bbox_max": [maxs.x, maxs.y, maxs.z],
        "dimensions_m": [dims.x, dims.y, dims.z],
        "length_m": max(dims.x, dims.y, dims.z),
        "material_count": mat_stats["material_count"],
        "texture_count": mat_stats["texture_count"],
        "missing_textures": mat_stats["missing_textures"],
        "alpha_fixed": mat_stats["alpha_fixed"],
        "backface_culling_disabled": mat_stats["backface_culling_disabled"],
        "muzzle_socket_name": args.weapon_muzzle_socket_name,
        "weapon_built": True,
        "weapon_embedded": False,
        "removed_objects": removed_objects,
        "exported_object_names": exported_object_names,
        "scale_factor_applied": scale_factor,
        "weapon_path": str(Path(args.out)),
        "export_path": str(Path(args.out)),
        "thumbnail_path": str(Path(args.thumb)),
        "blend_path": str(Path(args.blend)),
        "input_model": str(input_model),
        "timings": timings,
    }
    return report


def _run_normalize(args: argparse.Namespace, OPS) -> dict:
    normalize_mode = args.mode
    if normalize_mode == "generic" and args.character:
        normalize_mode = "character"
    if normalize_mode == "weapon":
        return _run_weapon_normalize(args, OPS)

    input_model = Path(args.input_model)
    if args.target_height_m is None or args.target_height_m <= 0:
        raise ValueError("--target_height_m must be provided and > 0 in normalize mode")
    if args.target_tris is None or args.target_tris <= 0:
        raise ValueError("--target_tris must be provided and > 0 in normalize mode")

    timings: dict[str, float] = {}
    reasons: list[str] = []
    t0 = time.perf_counter()
    OPS.wipe_scene()
    timings["wipe_scene_s"] = time.perf_counter() - t0

    t_import = time.perf_counter()
    imported = OPS.import_glb(input_model)
    timings["import_glb_s"] = time.perf_counter() - t_import
    if not imported:
        raise RuntimeError(f"No mesh objects found after importing {input_model}")

    objects_before = len(imported)
    armature = OPS.pick_primary_armature(imported)
    rig_exists = armature is not None
    if _object_exists(armature):
        if hasattr(armature.data, "display_type"):
            armature.data.display_type = "STICK"
    armature_transform_applied = False
    requested_clip_names = [c.strip() for c in list(args.requested_clip or []) if isinstance(c, str) and c.strip()]
    retarget_map_payload = _load_retarget_map(args.retarget_map)
    named_clip_entries = _parse_anim_clip_entries(list(args.anim_clip or []))
    canonical_t_pose_offsets_applied = 0

    exportable_meshes = OPS.collect_exportable_meshes(imported, armature)
    if not exportable_meshes:
        exportable_meshes = imported

    removed_objects: list[str] = []
    non_exportable = [obj for obj in imported if obj not in exportable_meshes]
    small_noise = [obj for obj in non_exportable if OPS.object_triangle_count(obj) < 300]
    helper_shapes = [obj for obj in OPS.bone_custom_shape_objects(armature) if _object_exists(obj)]
    to_remove: list[bpy.types.Object] = []
    seen_remove: set[str] = set()
    for obj in small_noise + helper_shapes:
        if obj.name in seen_remove:
            continue
        seen_remove.add(obj.name)
        to_remove.append(obj)
    removed_objects.extend(OPS.delete_objects(to_remove))

    t_join = time.perf_counter()
    joined = False
    if armature is None and len(exportable_meshes) > 1:
        exportable_meshes, joined = OPS.safe_join_objects(exportable_meshes)
    timings["join_meshes_s"] = time.perf_counter() - t_join

    t_loose = time.perf_counter()
    cleanup_loose_parts_skipped = False
    cleanup_loose_min_part_tris = 200
    cleanup_loose_max_removed_ratio: float | None = None
    cleanup_far_max_part_tris: int | None = None
    cleanup_far_min_distance_m: float | None = None
    cleanup_far_max_removed_ratio: float | None = None
    floating_components_before = 0
    floating_components_after = 0
    floating_components_post_scale_before = 0
    floating_components_post_budget_before = 0
    removed_loose_parts = 0
    removed_loose_names: list[str] = []
    removed_loose_by_size = 0
    removed_loose_by_distance = 0
    if rig_exists and normalize_mode == "character":
        # Safe cleanup for rigged meshes: remove only tiny loose shards.
        cleanup_loose_min_part_tris = 120
        cleanup_loose_max_removed_ratio = 0.03
        # Target obviously detached floating shards while preserving most disconnected clothing chunks.
        cleanup_far_max_part_tris = 120
        cleanup_far_min_distance_m = 0.20
        cleanup_far_max_removed_ratio = 0.30
        floating_components_before = OPS.count_far_small_components(
            exportable_meshes,
            armature_obj=armature,
            max_part_tris=cleanup_far_max_part_tris,
            min_distance_m=cleanup_far_min_distance_m,
        )
    exportable_meshes, removed_loose_parts, removed_loose_names, loose_stats = OPS.cleanup_loose_parts(
        exportable_meshes,
        min_part_tris=cleanup_loose_min_part_tris,
        max_removed_tris_ratio=cleanup_loose_max_removed_ratio,
        armature_obj=armature,
        drop_far_max_part_tris=cleanup_far_max_part_tris,
        drop_far_min_distance_m=cleanup_far_min_distance_m,
        max_far_removed_tris_ratio=cleanup_far_max_removed_ratio,
    )
    removed_loose_by_size = int(loose_stats.get("removed_by_size", 0))
    removed_loose_by_distance = int(loose_stats.get("removed_by_distance", 0))
    if rig_exists and normalize_mode == "character" and cleanup_far_max_part_tris and cleanup_far_min_distance_m:
        floating_components_after = OPS.count_far_small_components(
            exportable_meshes,
            armature_obj=armature,
            max_part_tris=cleanup_far_max_part_tris,
            min_distance_m=cleanup_far_min_distance_m,
        )
    timings["cleanup_loose_parts_s"] = time.perf_counter() - t_loose

    if not exportable_meshes:
        raise RuntimeError("All exportable meshes were removed during cleanup")

    transform_group: list[bpy.types.Object] = list(exportable_meshes)
    if _object_exists(armature):
        transform_group.append(armature)
    group_transform_objects: list[bpy.types.Object] = list(transform_group)
    if _object_exists(armature):
        unparented = [obj for obj in exportable_meshes if obj.parent != armature]
        group_transform_objects = [armature] + unparented

    scale_stats_before = OPS.armature_and_mesh_unit_scale_stats(armature_obj=armature, meshes=exportable_meshes)

    t_anim = time.perf_counter()
    clip_names, clip_errors, retargeted_clips = _import_and_attach_animation_clips(
        base_armature=armature,
        anim_model_paths=list(args.anim_model or []),
        anim_clip_entries=named_clip_entries,
        retarget_map_payload=retarget_map_payload,
    )
    # Keep one-action evaluation deterministic in viewport and bake/export passes.
    _disable_nla_evaluation(armature)
    timings["import_animation_clips_s"] = time.perf_counter() - t_anim
    reasons.extend(clip_errors)

    if _object_exists(armature) and args.canonical_pose == "T":
        t_pose = time.perf_counter()
        canonical_t_pose_offsets_applied = _apply_t_pose_offsets_from_map(armature, retarget_map_payload)
        timings["canonical_t_pose_s"] = time.perf_counter() - t_pose

    strip_scale_pre = OPS.strip_bone_scale_fcurves(_collect_scene_actions())
    bone_scale_pre_stats = OPS.count_scale_keys(_collect_scene_actions())
    unit_pose_bones_touched = OPS.force_pose_bone_unit_scale(armature)

    root_motion_zeroed = False
    root_motion_max_xy = 0.0
    root_motion_root_bone = ""
    root_motion_actions_touched = 0
    if bool(args.in_place) and _object_exists(armature):
        t_root = time.perf_counter()
        root_motion = OPS.zero_root_motion_xy(armature)
        timings["zero_root_motion_s"] = time.perf_counter() - t_root
        root_motion_root_bone = str(root_motion.get("root_bone") or "")
        root_motion_actions_touched = int(root_motion.get("actions_touched") or 0)
        root_motion_max_xy = float(root_motion.get("max_xy_before") or 0.0)
        root_motion_zeroed = root_motion_actions_touched > 0

    armature_pose_position = ""
    if _object_exists(armature) and hasattr(armature.data, "pose_position"):
        armature.data.pose_position = "REST"
        bpy.context.view_layer.update()
        armature_pose_position = str(armature.data.pose_position)

    t_apply = time.perf_counter()
    OPS.apply_object_transforms(group_transform_objects)
    armature_transform_applied = _object_exists(armature)
    timings["apply_transforms_s"] = time.perf_counter() - t_apply
    scale_stats_after = OPS.armature_and_mesh_unit_scale_stats(armature_obj=armature, meshes=exportable_meshes)

    t_weights = time.perf_counter()
    weight_stats = OPS.cleanup_skin_weights(
        meshes=exportable_meshes,
        armature_obj=armature,
        max_influences=4,
        tiny_weight=0.0001,
        aggressive=bool(args.aggressive_character_fix),
    )
    timings["cleanup_skin_weights_s"] = time.perf_counter() - t_weights

    t_normals = time.perf_counter()
    for obj in exportable_meshes:
        OPS.recalc_normals(obj)
        OPS.op_shade_smooth(obj)
        OPS.enable_auto_smooth(obj)
        OPS.apply_weighted_normal(obj)
    timings["fix_normals_s"] = time.perf_counter() - t_normals

    primary_rgba = _color_to_rgba(args.primary_color, (0.7, 0.7, 0.7, 1.0))
    secondary_rgba = _color_to_rgba(args.secondary_color, (0.35, 0.4, 0.35, 1.0))

    t_materials = time.perf_counter()
    mat_stats = OPS.sanitize_character_materials(
        exportable_meshes,
        primary_rgba=primary_rgba,
        secondary_rgba=secondary_rgba,
        disable_backface_culling=(normalize_mode == "character"),
    )
    timings["sanitize_materials_s"] = time.perf_counter() - t_materials

    t_pack = time.perf_counter()
    OPS.pack_all_images()
    timings["pack_images_s"] = time.perf_counter() - t_pack

    t_scale = time.perf_counter()
    applied_scale_factor = OPS.scale_group_to_height(
        measure_objects=exportable_meshes,
        transform_objects=group_transform_objects,
        target_height=float(args.target_height_m),
    )
    timings["scale_to_height_s"] = time.perf_counter() - t_scale

    t_ground = time.perf_counter()
    ground_center_delta = OPS.move_group_to_ground_center(
        measure_objects=exportable_meshes,
        transform_objects=group_transform_objects,
    )
    timings["ground_origin_s"] = time.perf_counter() - t_ground

    # Re-run floating shard cleanup after scale/ground so distance thresholds
    # are evaluated in final world-space dimensions.
    t_loose_post = time.perf_counter()
    if rig_exists and normalize_mode == "character" and cleanup_far_max_part_tris and cleanup_far_min_distance_m:
        previous_pose_position = None
        if _object_exists(armature) and hasattr(armature.data, "pose_position"):
            previous_pose_position = str(armature.data.pose_position)
            armature.data.pose_position = "POSE"
            bpy.context.view_layer.update()
        floating_components_post_scale_before = OPS.count_far_small_components(
            exportable_meshes,
            armature_obj=armature,
            max_part_tris=cleanup_far_max_part_tris,
            min_distance_m=cleanup_far_min_distance_m,
        )
        (
            exportable_meshes,
            removed_loose_post,
            removed_loose_names_post,
            loose_stats_post,
        ) = OPS.cleanup_loose_parts(
            exportable_meshes,
            min_part_tris=cleanup_loose_min_part_tris,
            max_removed_tris_ratio=cleanup_loose_max_removed_ratio,
            armature_obj=armature,
            drop_far_max_part_tris=cleanup_far_max_part_tris,
            drop_far_min_distance_m=cleanup_far_min_distance_m,
            max_far_removed_tris_ratio=cleanup_far_max_removed_ratio,
        )
        removed_loose_parts += int(removed_loose_post)
        removed_loose_names.extend(removed_loose_names_post)
        removed_loose_by_size += int(loose_stats_post.get("removed_by_size", 0))
        removed_loose_by_distance += int(loose_stats_post.get("removed_by_distance", 0))
        floating_components_after = OPS.count_far_small_components(
            exportable_meshes,
            armature_obj=armature,
            max_part_tris=cleanup_far_max_part_tris,
            min_distance_m=cleanup_far_min_distance_m,
        )
        if previous_pose_position is not None and _object_exists(armature):
            armature.data.pose_position = previous_pose_position
            bpy.context.view_layer.update()
    timings["cleanup_loose_parts_post_scale_s"] = time.perf_counter() - t_loose_post
    removed_objects.extend(removed_loose_names)
    if not exportable_meshes:
        raise RuntimeError("All exportable meshes were removed during post-scale cleanup")
    if rig_exists and _object_exists(armature) and len(exportable_meshes) > 1:
        # Keep rigged character output to a single skinned mesh object.
        for mesh in exportable_meshes:
            if _object_exists(mesh):
                mesh.parent = None
        exportable_meshes, _ = OPS.safe_join_objects(exportable_meshes)
        for mesh in exportable_meshes:
            if not _object_exists(mesh):
                continue
            if not OPS.mesh_has_armature_modifier(mesh, armature):
                mod = mesh.modifiers.new(name="Armature", type="ARMATURE")
                mod.object = armature
            mesh.parent = armature

    t_tri = time.perf_counter()
    for obj in exportable_meshes:
        OPS.triangulate_object(obj)
    timings["triangulate_s"] = time.perf_counter() - t_tri

    t_budget0 = time.perf_counter()
    tris_before_lod0 = OPS.count_triangles(exportable_meshes)
    tris_lod0 = OPS.enforce_target_tris(exportable_meshes, int(args.target_tris))
    timings["enforce_budget_lod0_s"] = time.perf_counter() - t_budget0

    t_loose_budget = time.perf_counter()
    if rig_exists and normalize_mode == "character" and cleanup_far_max_part_tris and cleanup_far_min_distance_m:
        previous_pose_position_budget = None
        if _object_exists(armature) and hasattr(armature.data, "pose_position"):
            previous_pose_position_budget = str(armature.data.pose_position)
            armature.data.pose_position = "POSE"
            bpy.context.view_layer.update()
        floating_components_post_budget_before = OPS.count_far_small_components(
            exportable_meshes,
            armature_obj=armature,
            max_part_tris=cleanup_far_max_part_tris,
            min_distance_m=cleanup_far_min_distance_m,
        )
        (
            exportable_meshes,
            removed_loose_budget,
            removed_loose_names_budget,
            loose_stats_budget,
        ) = OPS.cleanup_loose_parts(
            exportable_meshes,
            min_part_tris=cleanup_loose_min_part_tris,
            max_removed_tris_ratio=cleanup_loose_max_removed_ratio,
            armature_obj=armature,
            drop_far_max_part_tris=cleanup_far_max_part_tris,
            drop_far_min_distance_m=cleanup_far_min_distance_m,
            max_far_removed_tris_ratio=cleanup_far_max_removed_ratio,
        )
        removed_loose_parts += int(removed_loose_budget)
        if not exportable_meshes:
            raise RuntimeError("All exportable meshes were removed during post-budget cleanup")
        removed_loose_names.extend(removed_loose_names_budget)
        removed_loose_by_size += int(loose_stats_budget.get("removed_by_size", 0))
        removed_loose_by_distance += int(loose_stats_budget.get("removed_by_distance", 0))
        if rig_exists and _object_exists(armature) and len(exportable_meshes) > 1:
            for mesh in exportable_meshes:
                if _object_exists(mesh):
                    mesh.parent = None
            exportable_meshes, _ = OPS.safe_join_objects(exportable_meshes)
            for mesh in exportable_meshes:
                if not _object_exists(mesh):
                    continue
                if not OPS.mesh_has_armature_modifier(mesh, armature):
                    mod = mesh.modifiers.new(name="Armature", type="ARMATURE")
                    mod.object = armature
                mesh.parent = armature
        floating_components_after = OPS.count_far_small_components(
            exportable_meshes,
            armature_obj=armature,
            max_part_tris=cleanup_far_max_part_tris,
            min_distance_m=cleanup_far_min_distance_m,
        )
        if previous_pose_position_budget is not None and _object_exists(armature):
            armature.data.pose_position = previous_pose_position_budget
            bpy.context.view_layer.update()
        tris_lod0 = OPS.count_triangles(exportable_meshes)
    timings["cleanup_loose_parts_post_budget_s"] = time.perf_counter() - t_loose_budget

    weapon_meshes: list[bpy.types.Object] = []
    weapon_muzzle: bpy.types.Object | None = None
    grip_r: bpy.types.Object | None = None
    grip_l: bpy.types.Object | None = None
    sight_anchor: bpy.types.Object | None = None
    ads_target: bpy.types.Object | None = None
    anchor_modes = {
        "grip_right_mode": "heuristic",
        "grip_left_mode": "heuristic",
        "sight_mode": "heuristic",
    }
    grip_right_offset = _parse_optional_vec3(args.grip_right_offset_m)
    grip_left_offset = _parse_optional_vec3(args.grip_left_offset_m)
    sight_offset = _parse_optional_vec3(args.sight_offset_m)
    ads_eye_offset_world = _parse_optional_vec3(args.ads_eye_offset_m)
    ads_enabled = bool(args.ads_enabled)
    requested_ads_clips = [str(name).strip() for name in list(args.ads_clip or []) if str(name).strip()]
    explicit_ads_head_bone = str(args.ads_head_bone).strip() if args.ads_head_bone else ""
    explicit_ads_spine_bones = [str(name).strip() for name in list(args.ads_spine_bone or []) if str(name).strip()]

    weapon_embedded = False
    if args.weapon_model:
        t_attach = time.perf_counter()
        try:
            (
                weapon_meshes,
                weapon_muzzle,
                grip_r,
                grip_l,
                sight_anchor,
                ads_target,
                anchor_modes,
                weapon_errors,
            ) = _attach_weapon_to_character(
                OPS=OPS,
                armature=armature,
                fallback_anchor=exportable_meshes[0] if exportable_meshes else None,
                weapon_model_path=Path(args.weapon_model),
                socket_bone_name=args.weapon_socket_bone_name,
                bone_semantic=args.weapon_bone_semantic,
                muzzle_name=args.weapon_muzzle_socket_name,
                offset_m=_parse_vec3(args.weapon_offset_m, (0.0, 0.0, 0.0)),
                rotation_deg=_parse_vec3(args.weapon_rotation_deg, (0.0, 0.0, 0.0)),
                uniform_scale=float(args.weapon_scale),
                grip_right_offset_m=grip_right_offset,
                grip_left_offset_m=grip_left_offset,
                sight_offset_m=sight_offset,
                ads_aim_distance_m=float(args.ads_aim_distance_m),
            )
            reasons.extend(weapon_errors)
            weapon_embedded = bool(weapon_meshes)
            if not weapon_embedded and not weapon_errors:
                reasons.append("weapon embed skipped: no weapon meshes after attach")
        except Exception as exc:  # noqa: BLE001
            reasons.append(f"weapon embed failed: {exc}")
        timings["attach_weapon_s"] = time.perf_counter() - t_attach

    # ADS/IK bake and downstream exports must run in POSE mode so animation data
    # and constraints evaluate against the animated rig, not rest-pose bind data.
    if _object_exists(armature) and hasattr(armature.data, "pose_position"):
        armature.data.pose_position = "POSE"
        bpy.context.view_layer.update()

    t_ik = time.perf_counter()
    ads_clips_targeted = _resolve_ads_clip_targets(
        ads_enabled=ads_enabled,
        requested_ads_clips=requested_ads_clips,
        requested_clips=requested_clip_names,
        exported_clips=clip_names,
    )
    ads_clips_baked: list[str] = []
    ads_qc_failures: list[str] = []
    left_hand_grip_error_cm_max = 0.0
    left_hand_grip_error_cm_max_raw = 0.0
    right_hand_grip_error_cm_max = 0.0
    head_aim_error_deg_max = 0.0
    head_aim_error_deg_max_raw = 0.0
    ads_eye_to_sight_m_max = 0.0
    ads_eye_weapon_alignment_deg_max = 0.0
    ads_clip_metrics: list[dict] = []
    ads_head_bone_resolved = ""
    ads_spine_bones_resolved: list[str] = []
    if ads_enabled:
        ads_result = _bake_ads_for_actions(
            armature_obj=armature,
            clip_names=ads_clips_targeted,
            grip_r=grip_r,
            grip_l=grip_l,
            sight_anchor=sight_anchor,
            muzzle=weapon_muzzle,
            retarget_map_payload=retarget_map_payload,
            ads_head_bone=explicit_ads_head_bone or None,
            ads_spine_bones=explicit_ads_spine_bones,
            ads_eye_offset_world=ads_eye_offset_world,
        )
        ads_clips_baked = [str(name) for name in ads_result.get("baked_actions", [])]
        left_hand_grip_error_cm_max_raw = float(ads_result.get("left_hand_grip_error_cm_max", 0.0))
        left_hand_grip_error_cm_max = left_hand_grip_error_cm_max_raw
        right_hand_grip_error_cm_max = float(ads_result.get("right_hand_grip_error_cm_max", 0.0))
        head_aim_error_deg_max_raw = float(ads_result.get("head_aim_error_deg_max", 0.0))
        head_aim_error_deg_max = head_aim_error_deg_max_raw
        ads_eye_to_sight_m_max = float(ads_result.get("ads_eye_to_sight_m_max", 0.0))
        ads_eye_weapon_alignment_deg_max = float(ads_result.get("ads_eye_weapon_alignment_deg_max", 0.0))
        ads_clip_metrics = [dict(item) for item in ads_result.get("ads_clip_metrics", []) if isinstance(item, dict)]
        ads_head_bone_resolved = str(ads_result.get("resolved_head_bone", "") or "")
        ads_spine_bones_resolved = [str(name) for name in ads_result.get("resolved_spine_bones", [])]
        ads_qc_failures.extend(str(v) for v in ads_result.get("ads_failures", []))

        targeted_keys = {name.strip().lower() for name in ads_clips_targeted if name.strip()}
        baked_keys = {name.strip().lower() for name in ads_clips_baked if name.strip()}
        missing_ads = [name for name in ads_clips_targeted if name.strip().lower() not in baked_keys]
        if missing_ads:
            ads_qc_failures.append("missing ADS baked clips: " + ", ".join(missing_ads))
        if left_hand_grip_error_cm_max > 3.0:
            ads_qc_failures.append(f"left_hand_grip_error_cm_max={left_hand_grip_error_cm_max:.2f} (>3.00)")
        if ads_eye_to_sight_m_max > 0.20:
            ads_qc_failures.append(f"ads_eye_to_sight_m_max={ads_eye_to_sight_m_max:.3f} (>0.200)")
        if ads_eye_weapon_alignment_deg_max > 15.0:
            ads_qc_failures.append(
                f"ads_eye_weapon_alignment_deg_max={ads_eye_weapon_alignment_deg_max:.2f} (>15.00)"
            )
        if targeted_keys and not baked_keys:
            ads_qc_failures.append("ADS enabled but no targeted clips were baked")
        ads_qc_failures = list(dict.fromkeys(v for v in ads_qc_failures if v.strip()))

        ik_baked_actions = ads_clips_baked
        hand_lock_error_cm_max = max(left_hand_grip_error_cm_max, right_hand_grip_error_cm_max)
    else:
        ik_baked_actions, hand_lock_error_cm_max = _bake_hand_locks_for_actions(
            armature_obj=armature,
            # Only bake hand locks for clips we actually retargeted. Baking native
            # clips against a weapon parented to hand sockets can collapse motion.
            clip_names=retargeted_clips,
            grip_r=grip_r,
            grip_l=grip_l,
            retarget_map_payload=retarget_map_payload,
        )
        ads_qc_failures = []
    timings["ik_bake_s"] = time.perf_counter() - t_ik
    # Grip/sight helpers are internal bake targets; keep them out of artist view and exports.
    OPS.delete_objects([obj for obj in [grip_r, grip_l, sight_anchor, ads_target] if _object_exists(obj)])

    strip_scale_post = OPS.strip_bone_scale_fcurves(_collect_scene_actions())
    bone_scale_post_stats = OPS.count_scale_keys(_collect_scene_actions())

    export_objects: list[bpy.types.Object] = list(exportable_meshes)
    if weapon_meshes:
        export_objects.extend(weapon_meshes)
    if weapon_muzzle is not None:
        export_objects.append(weapon_muzzle)
    if _object_exists(armature):
        export_objects.append(armature)
    exported_object_names = [obj.name for obj in export_objects if _object_exists(obj)]

    render_objects = list(exportable_meshes) + weapon_meshes
    if not render_objects:
        render_objects = exportable_meshes
    timings["render_thumb_s"] = _render_thumbnail(OPS, render_objects, Path(args.thumb), size=256)
    timings["export_glb_lod0_s"] = _export_glb_selected(Path(args.out), apply_transforms=True, selected_objects=export_objects)

    tris_lod1 = tris_lod0
    out_lod1 = Path(args.out_lod1) if args.out_lod1 else None
    target_tris_lod1 = int(args.target_tris_lod1) if args.target_tris_lod1 else int(args.target_tris)
    if out_lod1 is not None and target_tris_lod1 > 0:
        t_budget1 = time.perf_counter()
        tris_lod1 = OPS.enforce_target_tris(exportable_meshes, target_tris_lod1)
        timings["enforce_budget_lod1_s"] = time.perf_counter() - t_budget1
        timings["export_glb_lod1_s"] = _export_glb_selected(
            out_lod1,
            apply_transforms=True,
            selected_objects=export_objects,
        )

    # Keep animation preview/export in pose mode after normalization measurements.
    armature_pose_position_export = armature_pose_position
    if _object_exists(armature) and hasattr(armature.data, "pose_position"):
        armature.data.pose_position = "POSE"
        bpy.context.view_layer.update()
        armature_pose_position_export = str(armature.data.pose_position)
    _disable_nla_evaluation(armature)
    review_candidates: list[str] = []
    # Prefer ADS/idle rifle preview pose in saved .blend when available.
    review_candidates.extend(["idle_rifle_in_place", "idle_rifle", "idle"])
    review_candidates.extend(list(args.ads_clip or []))
    review_candidates.extend(list(requested_clip_names))
    review_candidates.extend(list(clip_names))
    review_preview_action, review_preview_frame = _set_review_preview_pose(
        armature_obj=armature,
        preferred_clip_names=review_candidates,
    )

    timings["save_blend_s"] = _save_blend(Path(args.blend))

    mins, maxs = OPS.bounds_min_max(render_objects)
    missing_clips = _missing_requested_clips(requested_clip_names, clip_names)
    if missing_clips:
        reasons.append("missing requested clips: " + ", ".join(missing_clips))
    bone_scale_keys_removed = int(strip_scale_pre.get("removed_scale_keys", 0)) + int(
        strip_scale_post.get("removed_scale_keys", 0)
    )
    bone_scale_keys_remaining = int(bone_scale_post_stats.get("scale_key_count", 0))
    if bone_scale_keys_remaining > 0:
        reasons.append(f"bone scale keys remaining after sanitize: {bone_scale_keys_remaining}")
    clip_motion_metrics, movement_qc_failures = _collect_clip_motion_metrics(
        armature_obj=armature,
        clip_names=clip_names,
        retarget_map_payload=retarget_map_payload,
    )
    locomotion_clips_checked = len(
        [
            name
            for name, metric in clip_motion_metrics.items()
            if str(metric.get("movement_kind", "other")) in {"idle", "walk", "run", "strafe"}
        ]
    )
    if bool(args.character_qc_enforced) and movement_qc_failures:
        reasons.append("movement qc failures: " + "; ".join(movement_qc_failures))
    if bool(args.character_qc_enforced) and ads_qc_failures:
        reasons.append("ads qc failures: " + "; ".join(ads_qc_failures))

    report = {
        "asset_name": input_model.stem,
        "mode": "normalize",
        "triangle_count": tris_lod0,
        "triangle_count_before_budget": tris_before_lod0,
        "target_tris": int(args.target_tris),
        "tris_lod0": tris_lod0,
        "tris_lod1": tris_lod1,
        "target_tris_lod1": target_tris_lod1,
        "objects_before": objects_before,
        "objects_after": len(exportable_meshes),
        "object_count_before": objects_before,
        "object_count": len(exportable_meshes),
        "joined_meshes": joined,
        "missing_uvs": OPS.missing_uv_objects(render_objects),
        "height_m": OPS.model_height(render_objects),
        "bbox_min": [mins.x, mins.y, mins.z],
        "bbox_max": [maxs.x, maxs.y, maxs.z],
        "material_count": mat_stats["material_count"],
        "texture_count": mat_stats["texture_count"],
        "missing_textures": mat_stats["missing_textures"],
        "alpha_fixed": mat_stats["alpha_fixed"],
        "backface_culling_disabled": mat_stats["backface_culling_disabled"],
        "rig_present": rig_exists,
        "armature_transform_applied": armature_transform_applied,
        "armature_mesh_scale_before": scale_stats_before,
        "armature_mesh_scale_after": scale_stats_after,
        "animations_present": OPS.animations_present() or bool(clip_names),
        "requested_clips": requested_clip_names,
        "clip_names": clip_names,
        "missing_clips": missing_clips,
        "clip_count": len(clip_names),
        "clip_errors": clip_errors,
        "retargeted_clips": retargeted_clips,
        "ik_baked_actions": ik_baked_actions,
        "hand_lock_error_cm_max": hand_lock_error_cm_max,
        "ads_enabled": ads_enabled,
        "ads_clips_targeted": ads_clips_targeted,
        "ads_clips_baked": ads_clips_baked,
        "ads_qc_failures": ads_qc_failures,
        "left_hand_grip_error_cm_max": left_hand_grip_error_cm_max,
        "left_hand_grip_error_cm_max_raw": left_hand_grip_error_cm_max_raw,
        "right_hand_grip_error_cm_max": right_hand_grip_error_cm_max,
        "head_aim_error_deg_max": head_aim_error_deg_max,
        "head_aim_error_deg_max_raw": head_aim_error_deg_max_raw,
        "ads_eye_to_sight_m_max": ads_eye_to_sight_m_max,
        "ads_eye_weapon_alignment_deg_max": ads_eye_weapon_alignment_deg_max,
        "ads_clip_metrics": ads_clip_metrics,
        "ads_aim_distance_m": float(args.ads_aim_distance_m),
        "ads_eye_offset_m": list(ads_eye_offset_world) if ads_eye_offset_world is not None else None,
        "ads_head_bone": explicit_ads_head_bone,
        "ads_spine_bones": explicit_ads_spine_bones,
        "ads_head_bone_resolved": ads_head_bone_resolved,
        "ads_spine_bones_resolved": ads_spine_bones_resolved,
        "grip_right_offset_m": list(grip_right_offset) if grip_right_offset is not None else None,
        "grip_left_offset_m": list(grip_left_offset) if grip_left_offset is not None else None,
        "sight_offset_m": list(sight_offset) if sight_offset is not None else None,
        "weapon_anchor_modes": anchor_modes,
        "clip_motion_metrics": clip_motion_metrics,
        "movement_qc_failures": movement_qc_failures,
        "locomotion_clips_checked": locomotion_clips_checked,
        "removed_objects": removed_objects,
        "removed_loose_parts": removed_loose_parts,
        "removed_loose_parts_by_size": removed_loose_by_size,
        "removed_loose_parts_by_distance": removed_loose_by_distance,
        "cleanup_loose_parts_skipped": cleanup_loose_parts_skipped,
        "cleanup_loose_min_part_tris": cleanup_loose_min_part_tris,
        "cleanup_loose_max_removed_ratio": cleanup_loose_max_removed_ratio,
        "cleanup_far_max_part_tris": cleanup_far_max_part_tris,
        "cleanup_far_min_distance_m": cleanup_far_min_distance_m,
        "cleanup_far_max_removed_ratio": cleanup_far_max_removed_ratio,
        "floating_components_before": floating_components_before,
        "floating_components_post_scale_before": floating_components_post_scale_before,
        "floating_components_post_budget_before": floating_components_post_budget_before,
        "floating_components_after": floating_components_after,
        "root_motion_zeroed": root_motion_zeroed,
        "root_motion_max_xy": root_motion_max_xy,
        "root_motion_root_bone": root_motion_root_bone,
        "root_motion_actions_touched": root_motion_actions_touched,
        "armature_pose_position": armature_pose_position,
        "armature_pose_position_export": armature_pose_position_export,
        "review_preview_action": review_preview_action,
        "review_preview_frame": review_preview_frame,
        "canonical_pose": args.canonical_pose,
        "canonical_t_pose_offsets_applied": canonical_t_pose_offsets_applied,
        "retarget_map_path": args.retarget_map or "",
        "exported_object_names": exported_object_names,
        "scale_factor_applied": applied_scale_factor,
        "ground_center_delta": [ground_center_delta[0], ground_center_delta[1], ground_center_delta[2]],
        "weapon_path": args.weapon_model or "",
        "weapon_built": bool(args.weapon_model and Path(args.weapon_model).exists()),
        "weapon_embedded": weapon_embedded,
        "muzzle_socket_name": args.weapon_muzzle_socket_name if args.weapon_model else "",
        "bone_scale_keys_removed": bone_scale_keys_removed,
        "bone_scale_keys_remaining": bone_scale_keys_remaining,
        "bone_scale_keys_before": int(bone_scale_pre_stats.get("scale_key_count", 0)),
        "max_vertex_influences_before": int(weight_stats.get("max_vertex_influences_before", 0)),
        "max_vertex_influences_after": int(weight_stats.get("max_vertex_influences_after", 0)),
        "weight_sum_error_max_before": float(weight_stats.get("weight_sum_error_max_before", 0.0)),
        "weight_sum_error_max_after": float(weight_stats.get("weight_sum_error_max_after", 0.0)),
        "empty_vertex_groups_removed": int(weight_stats.get("empty_vertex_groups_removed", 0)),
        "unit_pose_bones_touched": unit_pose_bones_touched,
        "character_qc_enforced": bool(args.character_qc_enforced),
        "aggressive_character_fix": bool(args.aggressive_character_fix),
        "reasons": reasons,
        "export_path": str(Path(args.out)),
        "lod1_export_path": str(out_lod1) if out_lod1 else None,
        "thumbnail_path": str(Path(args.thumb)),
        "blend_path": str(Path(args.blend)),
        "input_model": str(input_model),
        "timings": timings,
        "status": "degraded" if reasons else "ok",
    }
    return report


def _main(argv: list[str]) -> int:
    args = _parse_args(argv)
    OPS = _load_blender_ops()

    report_path = Path(args.report)
    log_path = Path(args.log) if args.log else None

    if args.recipe:
        report = _run_procedural(args, OPS)
    else:
        report = _run_normalize(args, OPS)

    _write_report(report_path, report)

    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write("\n[blender_runner] completed successfully\n")

    return 0


def main() -> int:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    try:
        return _main(argv)
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()

        if "--" in sys.argv:
            parsed = _parse_args(sys.argv[sys.argv.index("--") + 1 :])
            if parsed.report:
                report = {
                    "status": "error",
                    "error": str(exc),
                }
                _write_report(Path(parsed.report), report)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
