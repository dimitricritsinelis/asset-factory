import argparse
import importlib.util
import json
import math
from pathlib import Path
import sys
import time
import traceback

import bpy
from mathutils import Vector


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
    parser.add_argument("--requested_clip", action="append", default=[], help="Requested output clip names")
    parser.add_argument("--character", action="store_true", help="Enable character-safe material defaults")
    parser.add_argument("--weapon_model", type=str, required=False, help="Weapon GLB to embed into character")
    parser.add_argument("--weapon_socket_bone_name", type=str, default="weapon_socket_r")
    parser.add_argument("--weapon_bone_semantic", type=str, default="right_hand")
    parser.add_argument("--weapon_muzzle_socket_name", type=str, default="muzzle")
    parser.add_argument("--weapon_offset_m", type=str, default="0,0,0")
    parser.add_argument("--weapon_rotation_deg", type=str, default="0,0,0")
    parser.add_argument("--weapon_scale", type=float, default=1.0)
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
        if "hand" in n and ("right" in n or ".r" in n or "_r" in n):
            return bone.name
    return None


def _ensure_socket_bone(
    armature_obj: bpy.types.Object,
    *,
    socket_bone_name: str,
    bone_semantic: str,
) -> str:
    if armature_obj.type != "ARMATURE":
        raise RuntimeError("Socket bone creation requires an armature object")

    if armature_obj.data.bones.get(socket_bone_name) is not None:
        return socket_bone_name

    parent_name: str | None = None
    if bone_semantic.strip().lower() == "right_hand":
        parent_name = _find_right_hand_bone_name(armature_obj)
    if parent_name is None and armature_obj.data.bones:
        parent_name = armature_obj.data.bones[0].name
    if parent_name is None:
        raise RuntimeError("Could not resolve a parent bone for socket creation")

    bpy.ops.object.select_all(action="DESELECT")
    armature_obj.select_set(True)
    bpy.context.view_layer.objects.active = armature_obj
    bpy.ops.object.mode_set(mode="EDIT")

    edit_bones = armature_obj.data.edit_bones
    parent = edit_bones.get(parent_name)
    if parent is None:
        bpy.ops.object.mode_set(mode="OBJECT")
        raise RuntimeError(f"Parent bone not found while creating socket: {parent_name}")

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


def _ensure_weapon_muzzle_empty(
    *,
    OPS,
    weapon_meshes: list[bpy.types.Object],
    imported_objects: list[bpy.types.Object],
    muzzle_name: str,
) -> bpy.types.Object:
    muzzle = next((o for o in imported_objects if o.type == "EMPTY" and o.name == muzzle_name), None)
    loc = _pick_muzzle_location(OPS, weapon_meshes)
    if muzzle is None:
        bpy.ops.object.empty_add(type="PLAIN_AXES", location=loc)
        muzzle = bpy.context.active_object
        muzzle.name = muzzle_name
    else:
        muzzle.location = loc
    return muzzle


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
) -> tuple[list[bpy.types.Object], bpy.types.Object | None, list[str]]:
    reasons: list[str] = []
    if not weapon_model_path.exists():
        return [], None, [f"weapon embed skipped: model not found ({weapon_model_path})"]

    imported_objects = _import_new_objects(weapon_model_path)
    weapon_meshes = [o for o in imported_objects if o.type == "MESH"]
    if not weapon_meshes:
        for obj in imported_objects:
            if obj.name in bpy.data.objects:
                bpy.data.objects.remove(obj, do_unlink=True)
        return [], None, ["weapon embed skipped: imported weapon has no mesh objects"]

    imported_armatures = [o for o in imported_objects if o.type == "ARMATURE"]
    OPS.delete_objects(imported_armatures)

    weapon_meshes, _ = OPS.safe_join_objects(weapon_meshes)
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
        for mesh in weapon_meshes:
            mesh.parent = armature
            mesh.parent_type = "BONE"
            mesh.parent_bone = socket_name
            mesh.location = Vector(offset_m)
            mesh.rotation_mode = "XYZ"
            mesh.rotation_euler = tuple(math.radians(v) for v in rotation_deg)
            mesh.scale = (uniform_scale, uniform_scale, uniform_scale)
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

    muzzle = _ensure_weapon_muzzle_empty(
        OPS=OPS,
        weapon_meshes=weapon_meshes,
        imported_objects=[o for o in imported_objects if o.name in bpy.data.objects],
        muzzle_name=muzzle_name,
    )
    muzzle.parent = weapon_meshes[0]
    muzzle.matrix_parent_inverse = weapon_meshes[0].matrix_world.inverted()
    return weapon_meshes, muzzle, reasons


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
    valid = [obj for obj in selected_objects if obj is not None and obj.name in bpy.data.objects]
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
    )
    return time.perf_counter() - t0


def _save_blend(blend_path: Path) -> float:
    t0 = time.perf_counter()
    blend_path.parent.mkdir(parents=True, exist_ok=True)
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


def _import_and_attach_animation_clips(
    *,
    base_armature: bpy.types.Object | None,
    anim_model_paths: list[str],
) -> tuple[list[str], list[str]]:
    if base_armature is None:
        return [], ["no base armature available for animation attachment"]
    if not anim_model_paths:
        return [], []

    clip_names: list[str] = []
    errors: list[str] = []

    if base_armature.animation_data is None:
        base_armature.animation_data_create()

    for raw in anim_model_paths:
        path = Path(raw)
        if not path.exists():
            errors.append(f"animation source missing: {path}")
            continue

        imported = _import_new_objects(path)
        src_arm = _first_armature(imported)
        if src_arm is None:
            errors.append(f"no armature in animation source: {path.name}")
            for obj in imported:
                if obj.name in bpy.data.objects:
                    bpy.data.objects.remove(obj, do_unlink=True)
            continue

        source_actions = _collect_actions_from_armature(src_arm)
        if not source_actions:
            errors.append(f"no actions found in animation source: {path.name}")
            for obj in imported:
                if obj.name in bpy.data.objects:
                    bpy.data.objects.remove(obj, do_unlink=True)
            continue

        base_name = path.stem
        for idx, action in enumerate(source_actions):
            clip_name = base_name if idx == 0 else f"{base_name}_{idx+1}"
            copied = action.copy()
            copied.name = clip_name
            track = base_armature.animation_data.nla_tracks.new()
            track.name = clip_name
            start = int(max(1.0, float(copied.frame_range[0])))
            track.strips.new(clip_name, start, copied)
            if base_armature.animation_data.action is None:
                base_armature.animation_data.action = copied
            clip_names.append(clip_name)

        for obj in imported:
            if obj.name in bpy.data.objects:
                bpy.data.objects.remove(obj, do_unlink=True)

    if clip_names and base_armature.animation_data and base_armature.animation_data.action is not None:
        action = base_armature.animation_data.action
        bpy.context.scene.frame_start = int(action.frame_range[0])
        bpy.context.scene.frame_end = int(action.frame_range[1])
        bpy.context.scene.frame_current = bpy.context.scene.frame_start

    return clip_names, errors


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
    armature_transform_applied = False
    requested_clip_names = [c.strip() for c in list(args.requested_clip or []) if isinstance(c, str) and c.strip()]

    exportable_meshes = OPS.collect_exportable_meshes(imported, armature)
    if not exportable_meshes:
        exportable_meshes = imported

    removed_objects: list[str] = []
    non_exportable = [obj for obj in imported if obj not in exportable_meshes]
    small_noise = [obj for obj in non_exportable if OPS.object_triangle_count(obj) < 300]
    helper_shapes = [
        obj for obj in OPS.bone_custom_shape_objects(armature)
        if obj is not None and obj.name in bpy.data.objects
    ]
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
    exportable_meshes, removed_loose_parts, removed_loose_names = OPS.cleanup_loose_parts(
        exportable_meshes, min_part_tris=200
    )
    removed_objects.extend(removed_loose_names)
    timings["cleanup_loose_parts_s"] = time.perf_counter() - t_loose

    if not exportable_meshes:
        raise RuntimeError("All exportable meshes were removed during cleanup")

    transform_group: list[bpy.types.Object] = list(exportable_meshes)
    if armature is not None and armature.name in bpy.data.objects:
        transform_group.append(armature)

    t_anim = time.perf_counter()
    clip_names, clip_errors = _import_and_attach_animation_clips(
        base_armature=armature,
        anim_model_paths=list(args.anim_model or []),
    )
    timings["import_animation_clips_s"] = time.perf_counter() - t_anim
    reasons.extend(clip_errors)

    t_apply = time.perf_counter()
    OPS.apply_object_transforms(transform_group)
    armature_transform_applied = armature is not None and armature.name in bpy.data.objects
    timings["apply_transforms_s"] = time.perf_counter() - t_apply

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
        transform_objects=transform_group,
        target_height=float(args.target_height_m),
    )
    timings["scale_to_height_s"] = time.perf_counter() - t_scale

    t_ground = time.perf_counter()
    ground_center_delta = OPS.move_group_to_ground_center(
        measure_objects=exportable_meshes,
        transform_objects=transform_group,
    )
    timings["ground_origin_s"] = time.perf_counter() - t_ground

    t_tri = time.perf_counter()
    for obj in exportable_meshes:
        OPS.triangulate_object(obj)
    timings["triangulate_s"] = time.perf_counter() - t_tri

    t_budget0 = time.perf_counter()
    tris_before_lod0 = OPS.count_triangles(exportable_meshes)
    tris_lod0 = OPS.enforce_target_tris(exportable_meshes, int(args.target_tris))
    timings["enforce_budget_lod0_s"] = time.perf_counter() - t_budget0

    weapon_meshes: list[bpy.types.Object] = []
    weapon_muzzle: bpy.types.Object | None = None
    weapon_embedded = False
    if args.weapon_model:
        t_attach = time.perf_counter()
        try:
            weapon_meshes, weapon_muzzle, weapon_errors = _attach_weapon_to_character(
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
            )
            reasons.extend(weapon_errors)
            weapon_embedded = bool(weapon_meshes)
            if not weapon_embedded and not weapon_errors:
                reasons.append("weapon embed skipped: no weapon meshes after attach")
        except Exception as exc:  # noqa: BLE001
            reasons.append(f"weapon embed failed: {exc}")
        timings["attach_weapon_s"] = time.perf_counter() - t_attach

    export_objects: list[bpy.types.Object] = list(exportable_meshes)
    if weapon_meshes:
        export_objects.extend(weapon_meshes)
    if weapon_muzzle is not None:
        export_objects.append(weapon_muzzle)
    if armature is not None and armature.name in bpy.data.objects:
        export_objects.append(armature)
    exported_object_names = [obj.name for obj in export_objects if obj.name in bpy.data.objects]

    render_objects = list(exportable_meshes) + weapon_meshes
    if not render_objects:
        render_objects = exportable_meshes
    timings["render_thumb_s"] = _render_thumbnail(OPS, render_objects, Path(args.thumb), size=256)
    timings["export_glb_lod0_s"] = _export_glb_selected(
        Path(args.out), apply_transforms=True, selected_objects=export_objects
    )

    tris_lod1 = tris_lod0
    out_lod1 = Path(args.out_lod1) if args.out_lod1 else None
    target_tris_lod1 = int(args.target_tris_lod1) if args.target_tris_lod1 else int(args.target_tris)
    if out_lod1 is not None and target_tris_lod1 > 0:
        t_budget1 = time.perf_counter()
        tris_lod1 = OPS.enforce_target_tris(exportable_meshes, target_tris_lod1)
        timings["enforce_budget_lod1_s"] = time.perf_counter() - t_budget1
        timings["export_glb_lod1_s"] = _export_glb_selected(
            out_lod1, apply_transforms=True, selected_objects=export_objects
        )

    timings["save_blend_s"] = _save_blend(Path(args.blend))

    mins, maxs = OPS.bounds_min_max(render_objects)
    missing_clips = _missing_requested_clips(requested_clip_names, clip_names)
    if missing_clips:
        reasons.append("missing requested clips: " + ", ".join(missing_clips))
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
        "animations_present": OPS.animations_present() or bool(clip_names),
        "requested_clips": requested_clip_names,
        "clip_names": clip_names,
        "missing_clips": missing_clips,
        "clip_count": len(clip_names),
        "clip_errors": clip_errors,
        "removed_objects": removed_objects,
        "removed_loose_parts": removed_loose_parts,
        "exported_object_names": exported_object_names,
        "scale_factor_applied": applied_scale_factor,
        "ground_center_delta": [ground_center_delta[0], ground_center_delta[1], ground_center_delta[2]],
        "weapon_path": args.weapon_model or "",
        "weapon_built": bool(args.weapon_model and Path(args.weapon_model).exists()),
        "weapon_embedded": weapon_embedded,
        "muzzle_socket_name": args.weapon_muzzle_socket_name if args.weapon_model else "",
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
