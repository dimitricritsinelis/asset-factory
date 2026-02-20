import math
from pathlib import Path

import bpy
from mathutils import Vector


def wipe_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)

    for datablock in [bpy.data.meshes, bpy.data.materials, bpy.data.textures, bpy.data.images, bpy.data.armatures]:
        for item in list(datablock):
            if item.users == 0:
                datablock.remove(item)


def _activate_object(obj: bpy.types.Object) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def create_primitive(primitive: str, name: str) -> bpy.types.Object:
    if primitive == "cube":
        bpy.ops.mesh.primitive_cube_add(size=1.0)
    elif primitive == "cylinder":
        bpy.ops.mesh.primitive_cylinder_add(radius=0.5, depth=1.0)
    elif primitive == "uv_sphere":
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.5)
    elif primitive == "ico_sphere":
        bpy.ops.mesh.primitive_ico_sphere_add(radius=0.5)
    elif primitive == "plane":
        bpy.ops.mesh.primitive_plane_add(size=1.0)
    else:
        raise ValueError(f"Unsupported primitive: {primitive}")

    obj = bpy.context.active_object
    obj.name = name
    return obj


def apply_transform(
    obj: bpy.types.Object,
    location: tuple[float, float, float],
    rotation_deg: tuple[float, float, float],
    scale: tuple[float, float, float],
) -> None:
    obj.location = location
    obj.rotation_euler = tuple(math.radians(v) for v in rotation_deg)
    obj.scale = scale


def _apply_modifier(obj: bpy.types.Object, modifier: bpy.types.Modifier) -> None:
    _activate_object(obj)
    bpy.ops.object.modifier_apply(modifier=modifier.name)


def op_bevel(obj: bpy.types.Object, width: float, segments: int) -> None:
    mod = obj.modifiers.new(name="Bevel", type="BEVEL")
    mod.width = width
    mod.segments = segments
    _apply_modifier(obj, mod)


def op_subdivide(obj: bpy.types.Object, levels: int) -> None:
    mod = obj.modifiers.new(name="Subdiv", type="SUBSURF")
    mod.levels = levels
    mod.render_levels = levels
    _apply_modifier(obj, mod)


def op_decimate(obj: bpy.types.Object, ratio: float) -> None:
    mod = obj.modifiers.new(name="Decimate", type="DECIMATE")
    mod.ratio = ratio
    _apply_modifier(obj, mod)


def op_shade_smooth(obj: bpy.types.Object) -> None:
    _activate_object(obj)
    bpy.ops.object.shade_smooth()


def op_mirror(obj: bpy.types.Object, axis: str) -> None:
    mod = obj.modifiers.new(name="Mirror", type="MIRROR")
    mod.use_axis[0] = axis == "X"
    mod.use_axis[1] = axis == "Y"
    mod.use_axis[2] = axis == "Z"
    _apply_modifier(obj, mod)


def op_noise_displace(obj: bpy.types.Object, strength: float, scale: float) -> None:
    tex = bpy.data.textures.new(name=f"NoiseTex_{obj.name}", type="CLOUDS")
    tex.noise_scale = scale
    mod = obj.modifiers.new(name="Displace", type="DISPLACE")
    mod.texture = tex
    mod.strength = strength
    _apply_modifier(obj, mod)


def apply_op(obj: bpy.types.Object, op: dict) -> None:
    op_type = op["type"]
    if op_type == "bevel":
        op_bevel(obj, float(op["width"]), int(op["segments"]))
    elif op_type == "subdivide":
        op_subdivide(obj, int(op["levels"]))
    elif op_type == "decimate":
        op_decimate(obj, float(op["ratio"]))
    elif op_type == "shade_smooth":
        op_shade_smooth(obj)
    elif op_type == "mirror":
        op_mirror(obj, str(op["axis"]))
    elif op_type == "noise_displace":
        op_noise_displace(obj, float(op["strength"]), float(op["scale"]))
    else:
        raise ValueError(f"Unsupported op: {op_type}")


def mesh_objects() -> list[bpy.types.Object]:
    return [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]


def armature_objects() -> list[bpy.types.Object]:
    return [obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE"]


def import_glb(path: Path) -> list[bpy.types.Object]:
    if not path.exists():
        raise FileNotFoundError(f"Input model not found: {path}")
    bpy.ops.import_scene.gltf(filepath=str(path))
    return mesh_objects()


def apply_object_transforms(objects: list[bpy.types.Object]) -> None:
    for obj in objects:
        _activate_object(obj)
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)


def join_objects(objects: list[bpy.types.Object]) -> bpy.types.Object:
    if not objects:
        raise ValueError("Cannot join zero objects")
    if len(objects) == 1:
        return objects[0]

    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.object.join()
    return bpy.context.active_object


def safe_join_objects(objects: list[bpy.types.Object]) -> tuple[list[bpy.types.Object], bool]:
    if len(objects) <= 1:
        return objects, False
    try:
        joined = join_objects(objects)
        return [joined], True
    except Exception:
        return objects, False


def object_triangle_count(obj: bpy.types.Object) -> int:
    if obj.type != "MESH":
        return 0
    mesh = obj.data
    mesh.calc_loop_triangles()
    return len(mesh.loop_triangles)


def pick_primary_armature(meshes: list[bpy.types.Object]) -> bpy.types.Object | None:
    arms = armature_objects()
    if not arms:
        return None
    if len(arms) == 1:
        return arms[0]

    # Pick the armature referenced by the most mesh armature modifiers.
    by_name = {a.name: a for a in arms}
    scores: dict[str, int] = {a.name: 0 for a in arms}
    for obj in meshes:
        for mod in obj.modifiers:
            arm_obj = getattr(mod, "object", None)
            if mod.type == "ARMATURE" and arm_obj is not None and arm_obj.name in scores:
                scores[arm_obj.name] += 1
    best_name = max(scores.items(), key=lambda kv: kv[1])[0]
    return by_name[best_name]


def mesh_has_armature_modifier(mesh_obj: bpy.types.Object, armature_obj: bpy.types.Object) -> bool:
    for mod in mesh_obj.modifiers:
        if mod.type == "ARMATURE" and getattr(mod, "object", None) == armature_obj:
            return True
    return False


def mesh_has_bone_group_match(mesh_obj: bpy.types.Object, armature_obj: bpy.types.Object) -> bool:
    arm_bones = {b.name for b in armature_obj.data.bones}
    if not arm_bones:
        return False
    vg_names = {vg.name for vg in mesh_obj.vertex_groups}
    return len(arm_bones.intersection(vg_names)) > 0


def collect_exportable_meshes(
    meshes: list[bpy.types.Object],
    armature_obj: bpy.types.Object | None,
) -> list[bpy.types.Object]:
    if armature_obj is None:
        return meshes

    selected: list[bpy.types.Object] = []
    for mesh in meshes:
        if mesh_has_armature_modifier(mesh, armature_obj) or mesh_has_bone_group_match(mesh, armature_obj):
            selected.append(mesh)
    return selected


def bone_custom_shape_objects(armature_obj: bpy.types.Object | None) -> list[bpy.types.Object]:
    out: list[bpy.types.Object] = []
    seen: set[str] = set()
    if armature_obj is None or armature_obj.type != "ARMATURE" or armature_obj.pose is None:
        return out
    for pbone in armature_obj.pose.bones:
        shape = getattr(pbone, "custom_shape", None)
        if shape is not None and shape.name not in seen:
            seen.add(shape.name)
            out.append(shape)
    return out


def delete_objects(objects: list[bpy.types.Object]) -> list[str]:
    removed_names: list[str] = []
    for obj in objects:
        if obj is None or obj.name not in bpy.data.objects:
            continue
        removed_names.append(obj.name)
        bpy.data.objects.remove(obj, do_unlink=True)
    return removed_names


def recalc_normals(obj: bpy.types.Object) -> None:
    _activate_object(obj)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.object.mode_set(mode="OBJECT")


def enable_auto_smooth(obj: bpy.types.Object) -> None:
    mesh = obj.data
    # Blender APIs changed over versions; guard for compatibility.
    if hasattr(mesh, "use_auto_smooth"):
        mesh.use_auto_smooth = True
        if hasattr(mesh, "auto_smooth_angle"):
            mesh.auto_smooth_angle = math.radians(70.0)


def apply_weighted_normal(obj: bpy.types.Object) -> None:
    try:
        mod = obj.modifiers.new(name="WeightedNormal", type="WEIGHTED_NORMAL")
    except Exception:
        return
    mod.keep_sharp = True
    _apply_modifier(obj, mod)


def cleanup_loose_parts(
    meshes: list[bpy.types.Object],
    *,
    min_part_tris: int = 200,
) -> tuple[list[bpy.types.Object], int, list[str]]:
    cleaned: list[bpy.types.Object] = []
    removed_count = 0
    removed_names: list[str] = []

    for obj in meshes:
        if obj is None or obj.name not in bpy.data.objects:
            continue

        _activate_object(obj)
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.separate(type="LOOSE")
        bpy.ops.object.mode_set(mode="OBJECT")

        parts = [o for o in bpy.context.selected_objects if o.type == "MESH"]
        if not parts:
            parts = [obj]

        if len(parts) == 1:
            cleaned.append(parts[0])
            continue

        part_tris = [(p, object_triangle_count(p)) for p in parts]
        kept = [p for p, tris in part_tris if tris >= min_part_tris]
        dropped = [p for p, tris in part_tris if tris < min_part_tris]

        # If every piece is "small", treat it as intentional segmentation and keep all.
        if not kept:
            kept = [p for p, _ in part_tris]
            dropped = []

        removed_count += len(dropped)
        removed_names.extend(delete_objects(dropped))

        if len(kept) > 1:
            joined, _ = safe_join_objects(kept)
            cleaned.extend(joined)
        elif kept:
            cleaned.extend(kept)

    # De-dup while preserving order.
    seen: set[str] = set()
    deduped: list[bpy.types.Object] = []
    for obj in cleaned:
        if obj.name in seen:
            continue
        seen.add(obj.name)
        deduped.append(obj)
    return deduped, removed_count, removed_names


def smart_uv_unwrap(obj: bpy.types.Object) -> None:
    _activate_object(obj)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(island_margin=0.02)
    bpy.ops.object.mode_set(mode="OBJECT")


def triangulate_object(obj: bpy.types.Object) -> None:
    mod = obj.modifiers.new(name="Triangulate", type="TRIANGULATE")
    _apply_modifier(obj, mod)


def count_triangles(objects: list[bpy.types.Object]) -> int:
    total = 0
    for obj in objects:
        mesh = obj.data
        mesh.calc_loop_triangles()
        total += len(mesh.loop_triangles)
    return total


def enforce_target_tris(objects: list[bpy.types.Object], target_tris: int) -> int:
    current = count_triangles(objects)
    if current <= target_tris:
        return current

    ratio = max(0.03, min(1.0, target_tris / float(current)))
    for obj in objects:
        op_decimate(obj, ratio)
    return count_triangles(objects)


def create_material(spec: dict, project_root: Path) -> bpy.types.Material:
    material = bpy.data.materials.new(name=spec["name"])
    material.use_nodes = True

    nt = material.node_tree
    nodes = nt.nodes
    links = nt.links

    bsdf = nodes.get("Principled BSDF")
    if bsdf is None:
        bsdf = nodes.new(type="ShaderNodeBsdfPrincipled")

    base = spec["basecolor"]
    if base["mode"] == "solid":
        bsdf.inputs["Base Color"].default_value = tuple(base["color_rgba"])
    elif base["mode"] == "texture":
        tex_path = Path(base["texture_path"])
        if not tex_path.is_absolute():
            tex_path = project_root / tex_path
        if not tex_path.exists():
            raise FileNotFoundError(f"Texture does not exist: {tex_path}")

        tex_node = nodes.new(type="ShaderNodeTexImage")
        tex_node.image = bpy.data.images.load(str(tex_path))
        links.new(tex_node.outputs["Color"], bsdf.inputs["Base Color"])
    else:
        raise ValueError(f"Unsupported basecolor mode: {base['mode']}")

    return material


def assign_material(obj: bpy.types.Object, material: bpy.types.Material) -> None:
    mesh = obj.data
    if len(mesh.materials) == 0:
        mesh.materials.append(material)
    else:
        mesh.materials[0] = material


def _fallback_material(primary_rgba: tuple[float, float, float, float]) -> bpy.types.Material:
    mat = bpy.data.materials.new(name="fallback_mat")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = primary_rgba
        bsdf.inputs["Alpha"].default_value = 1.0
    return mat


def sanitize_character_materials(
    objects: list[bpy.types.Object],
    primary_rgba: tuple[float, float, float, float],
    secondary_rgba: tuple[float, float, float, float],
    disable_backface_culling: bool,
) -> dict:
    missing_textures: list[str] = []
    alpha_fixed = False
    culling_disabled = False

    # Ensure every mesh has at least one material.
    fallback_primary = _fallback_material(primary_rgba)
    fallback_secondary = _fallback_material(secondary_rgba)

    for i, obj in enumerate(objects):
        mesh = obj.data
        if len(mesh.materials) == 0:
            mesh.materials.append(fallback_primary if i % 2 == 0 else fallback_secondary)

        for slot in mesh.materials:
            if slot is None:
                continue
            mat = slot
            mat.use_nodes = True
            if hasattr(mat, "blend_method"):
                mat.blend_method = "OPAQUE"
            if disable_backface_culling and getattr(mat, "use_backface_culling", None) is not None:
                if mat.use_backface_culling:
                    culling_disabled = True
                mat.use_backface_culling = False

            nt = mat.node_tree
            if nt is None:
                continue

            bsdf = nt.nodes.get("Principled BSDF")
            if bsdf is not None and "Alpha" in bsdf.inputs:
                if bsdf.inputs["Alpha"].default_value != 1.0:
                    alpha_fixed = True
                bsdf.inputs["Alpha"].default_value = 1.0
                for link in list(bsdf.inputs["Alpha"].links):
                    nt.links.remove(link)
                    alpha_fixed = True

            for node in nt.nodes:
                if node.type == "TEX_IMAGE":
                    image = getattr(node, "image", None)
                    if image is None:
                        missing_textures.append(f"{mat.name}:<missing-image>")
                        continue
                    if image.source == "FILE":
                        fp = bpy.path.abspath(image.filepath) if image.filepath else ""
                        if not fp or not Path(fp).exists():
                            missing_textures.append(f"{mat.name}:{image.filepath}")

            # If material has missing textures, force safe base color.
            if any(m.startswith(f"{mat.name}:") for m in missing_textures):
                if bsdf is not None and "Base Color" in bsdf.inputs:
                    bsdf.inputs["Base Color"].default_value = primary_rgba

    texture_count = count_textures()
    return {
        "missing_textures": sorted(set(missing_textures)),
        "alpha_fixed": alpha_fixed,
        "backface_culling_disabled": culling_disabled,
        "material_count": len([m for m in bpy.data.materials if m.users > 0]),
        "texture_count": texture_count,
    }


def missing_uv_objects(objects: list[bpy.types.Object]) -> list[str]:
    missing = []
    for obj in objects:
        if len(obj.data.uv_layers) == 0:
            missing.append(obj.name)
    return missing


def bounds_min_max(objects: list[bpy.types.Object]) -> tuple[Vector, Vector]:
    if not objects:
        return Vector((0.0, 0.0, 0.0)), Vector((0.0, 0.0, 0.0))

    mins = Vector((float("inf"), float("inf"), float("inf")))
    maxs = Vector((float("-inf"), float("-inf"), float("-inf")))
    for obj in objects:
        for corner in obj.bound_box:
            world = obj.matrix_world @ Vector(corner)
            mins.x = min(mins.x, world.x)
            mins.y = min(mins.y, world.y)
            mins.z = min(mins.z, world.z)
            maxs.x = max(maxs.x, world.x)
            maxs.y = max(maxs.y, world.y)
            maxs.z = max(maxs.z, world.z)
    return mins, maxs


def _bounds_center_and_radius(objects: list[bpy.types.Object]) -> tuple[Vector, float]:
    mins, maxs = bounds_min_max(objects)
    center = (mins + maxs) * 0.5
    radius = max((maxs - mins).length * 0.5, 0.2)
    return center, radius


def bounds_dimensions(objects: list[bpy.types.Object]) -> Vector:
    mins, maxs = bounds_min_max(objects)
    return maxs - mins


def model_height(objects: list[bpy.types.Object]) -> float:
    return max(0.0, bounds_dimensions(objects).z)


def scale_objects_to_height(objects: list[bpy.types.Object], target_height: float) -> float:
    if not objects:
        return 1.0
    current_h = model_height(objects)
    if current_h <= 1e-6:
        return 1.0
    scale_factor = target_height / current_h
    for obj in objects:
        obj.scale = obj.scale * scale_factor
    apply_object_transforms(objects)
    return scale_factor


def scale_objects_to_max_dimension(objects: list[bpy.types.Object], target_size: float) -> float:
    if not objects:
        return 1.0
    dims = bounds_dimensions(objects)
    current = max(dims.x, dims.y, dims.z)
    if current <= 1e-6:
        return 1.0
    scale_factor = target_size / current
    for obj in objects:
        obj.scale = obj.scale * scale_factor
    apply_object_transforms(objects)
    return scale_factor


def move_objects_to_ground_center(objects: list[bpy.types.Object]) -> None:
    if not objects:
        return
    mins, maxs = bounds_min_max(objects)
    cx = (mins.x + maxs.x) * 0.5
    cy = (mins.y + maxs.y) * 0.5
    dz = -mins.z
    for obj in objects:
        obj.location.x -= cx
        obj.location.y -= cy
        obj.location.z += dz
    apply_object_transforms(objects)


def count_textures() -> int:
    total = 0
    for image in bpy.data.images:
        if image.packed_file:
            total += 1
        elif getattr(image, "filepath", None):
            total += 1
    return total


def rig_present() -> bool:
    return len(armature_objects()) > 0


def animations_present() -> bool:
    for obj in armature_objects():
        ad = getattr(obj, "animation_data", None)
        if ad and (ad.action is not None or len(ad.nla_tracks) > 0):
            return True
    return any(action.users > 0 for action in bpy.data.actions)


def pack_all_images() -> None:
    bpy.ops.file.pack_all()


def setup_camera_and_light(
    objects: list[bpy.types.Object],
    camera_angle_deg: tuple[float, float, float],
    light_strength: float,
) -> None:
    center, radius = _bounds_center_and_radius(objects)
    base_dir = Vector((1.6, -1.7, 1.05)).normalized()
    distance = max(2.0, radius * 3.2)
    cam_location = center + base_dir * distance

    bpy.ops.object.camera_add(location=cam_location)
    camera = bpy.context.active_object
    look = center - cam_location
    camera.rotation_euler = look.to_track_quat("-Z", "Y").to_euler()
    if camera_angle_deg:
        camera.rotation_euler.rotate_axis("Z", math.radians(camera_angle_deg[2] * 0.08))
    bpy.context.scene.camera = camera

    bpy.ops.object.light_add(type="SUN", location=center + Vector((2.0, -2.0, 3.0)))
    light = bpy.context.active_object
    light.data.energy = float(light_strength)
