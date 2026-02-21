import bmesh
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


def _object_name(obj: bpy.types.Object | None) -> str | None:
    if obj is None:
        return None
    try:
        return obj.name
    except ReferenceError:
        return None


def object_exists(obj: bpy.types.Object | None) -> bool:
    name = _object_name(obj)
    return bool(name and name in bpy.data.objects)


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
        name = _object_name(obj)
        if not name or name not in bpy.data.objects:
            continue
        removed_names.append(name)
        bpy.data.objects.remove(bpy.data.objects[name], do_unlink=True)
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
    max_removed_tris_ratio: float | None = None,
    armature_obj: bpy.types.Object | None = None,
    drop_far_max_part_tris: int | None = None,
    drop_far_min_distance_m: float | None = None,
    max_far_removed_tris_ratio: float | None = None,
) -> tuple[list[bpy.types.Object], int, list[str], dict[str, int]]:
    def _point_segment_distance(point: Vector, a: Vector, b: Vector) -> float:
        ab = b - a
        den = float(ab.length_squared)
        if den <= 1e-12:
            return float((point - a).length)
        t = max(0.0, min(1.0, float((point - a).dot(ab) / den)))
        closest = a + (ab * t)
        return float((point - closest).length)

    def _armature_segments_world(arm_obj: bpy.types.Object | None) -> list[tuple[Vector, Vector]]:
        if arm_obj is None or arm_obj.type != "ARMATURE" or arm_obj.data is None:
            return []
        segments: list[tuple[Vector, Vector]] = []
        for bone in arm_obj.data.bones:
            head = arm_obj.matrix_world @ bone.head_local
            tail = arm_obj.matrix_world @ bone.tail_local
            segments.append((head, tail))
        return segments

    def _component_center_world(part: bpy.types.Object) -> Vector:
        mesh = getattr(part, "data", None)
        verts = list(getattr(mesh, "vertices", [])) if mesh is not None else []
        if verts:
            world_verts = [part.matrix_world @ vert.co for vert in verts]
            cx = sum(v.x for v in world_verts) / len(world_verts)
            cy = sum(v.y for v in world_verts) / len(world_verts)
            cz = sum(v.z for v in world_verts) / len(world_verts)
            return Vector((cx, cy, cz))
        corners = [part.matrix_world @ Vector(corner) for corner in part.bound_box]
        if not corners:
            return part.matrix_world.translation.copy()
        cx = sum(v.x for v in corners) / len(corners)
        cy = sum(v.y for v in corners) / len(corners)
        cz = sum(v.z for v in corners) / len(corners)
        return Vector((cx, cy, cz))

    bone_segments = _armature_segments_world(armature_obj)
    cleaned: list[bpy.types.Object] = []
    removed_count = 0
    removed_names: list[str] = []
    removed_by_size = 0
    removed_by_distance = 0

    for obj in meshes:
        if not object_exists(obj):
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
        kept_pairs = [(p, tris) for p, tris in part_tris if tris >= min_part_tris]
        dropped_size_pairs = [(p, tris) for p, tris in part_tris if tris < min_part_tris]
        dropped_far_pairs: list[tuple[bpy.types.Object, int]] = []
        if (
            bone_segments
            and drop_far_max_part_tris is not None
            and drop_far_min_distance_m is not None
            and drop_far_max_part_tris > 0
            and drop_far_min_distance_m > 0.0
        ):
            for part, tris in part_tris:
                if tris > drop_far_max_part_tris:
                    continue
                center = _component_center_world(part)
                min_dist = min(_point_segment_distance(center, a, b) for a, b in bone_segments)
                if min_dist >= float(drop_far_min_distance_m):
                    dropped_far_pairs.append((part, tris))

        # If every piece is "small", treat it as intentional segmentation and keep all.
        if not kept_pairs:
            kept_pairs = list(part_tris)
            dropped_size_pairs = []

        if max_removed_tris_ratio is not None and dropped_size_pairs:
            ratio = max(0.0, min(float(max_removed_tris_ratio), 0.95))
            total_tris = sum(tris for _, tris in part_tris)
            max_removed_tris = int(total_tris * ratio)
            removed_tris = sum(tris for _, tris in dropped_size_pairs)
            if removed_tris > max_removed_tris:
                # Keep the largest dropped chunks first until removed tris stay within budget.
                for part, tris in sorted(dropped_size_pairs, key=lambda it: it[1], reverse=True):
                    if removed_tris <= max_removed_tris:
                        break
                    kept_pairs.append((part, tris))
                    removed_tris -= tris
                kept_set = {id(p) for p, _ in kept_pairs}
                dropped_size_pairs = [(p, tris) for p, tris in dropped_size_pairs if id(p) not in kept_set]

        if max_far_removed_tris_ratio is not None and dropped_far_pairs:
            ratio = max(0.0, min(float(max_far_removed_tris_ratio), 0.95))
            total_tris = sum(tris for _, tris in part_tris)
            max_removed_far_tris = int(total_tris * ratio)
            removed_far_tris = sum(tris for _, tris in dropped_far_pairs)
            if removed_far_tris > max_removed_far_tris:
                for part, tris in sorted(dropped_far_pairs, key=lambda it: it[1], reverse=True):
                    if removed_far_tris <= max_removed_far_tris:
                        break
                    kept_pairs.append((part, tris))
                    removed_far_tris -= tris
                kept_set = {id(p) for p, _ in kept_pairs}
                dropped_far_pairs = [(p, tris) for p, tris in dropped_far_pairs if id(p) not in kept_set]

        # Ensure no object can be both kept and dropped by size-based cleanup.
        # Distance-based cleanup is allowed to override size keep if a part is
        # still classified as a detached floating shard.
        kept_ids = {id(p) for p, _ in kept_pairs}
        dropped_size_pairs = [(p, tris) for p, tris in dropped_size_pairs if id(p) not in kept_ids]

        dropped_pairs: list[tuple[bpy.types.Object, int]] = []
        seen_drop: set[int] = set()
        for pair in dropped_size_pairs + dropped_far_pairs:
            pid = id(pair[0])
            if pid in seen_drop:
                continue
            seen_drop.add(pid)
            dropped_pairs.append(pair)

        kept = [p for p, _ in kept_pairs]
        dropped = [p for p, _ in dropped_pairs]

        removed_count += len(dropped)
        removed_names.extend(delete_objects(dropped))
        dropped_far_ids = {id(p) for p, _ in dropped_far_pairs}
        for part, _ in dropped_pairs:
            if id(part) in dropped_far_ids:
                removed_by_distance += 1
            else:
                removed_by_size += 1

        if len(kept) > 1:
            joined, _ = safe_join_objects(kept)
            cleaned.extend(joined)
        elif kept:
            cleaned.extend(kept)

    # De-dup while preserving order.
    seen: set[str] = set()
    deduped: list[bpy.types.Object] = []
    for obj in cleaned:
        name = _object_name(obj)
        if not name or not object_exists(obj):
            continue
        if name in seen:
            continue
        seen.add(name)
        deduped.append(obj)
    stats = {
        "removed_by_size": removed_by_size,
        "removed_by_distance": removed_by_distance,
    }
    return deduped, removed_count, removed_names, stats


def count_far_small_components(
    meshes: list[bpy.types.Object],
    *,
    armature_obj: bpy.types.Object | None,
    max_part_tris: int,
    min_distance_m: float,
) -> int:
    if (
        armature_obj is None
        or armature_obj.type != "ARMATURE"
        or max_part_tris <= 0
        or min_distance_m <= 0.0
        or armature_obj.data is None
    ):
        return 0

    def _point_segment_distance(point: Vector, a: Vector, b: Vector) -> float:
        ab = b - a
        den = float(ab.length_squared)
        if den <= 1e-12:
            return float((point - a).length)
        t = max(0.0, min(1.0, float((point - a).dot(ab) / den)))
        closest = a + (ab * t)
        return float((point - closest).length)

    segments: list[tuple[Vector, Vector]] = []
    for bone in armature_obj.data.bones:
        head = armature_obj.matrix_world @ bone.head_local
        tail = armature_obj.matrix_world @ bone.tail_local
        segments.append((head, tail))
    if not segments:
        return 0

    floating_count = 0
    for obj in meshes:
        if not object_exists(obj) or obj.type != "MESH":
            continue
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        bm.verts.ensure_lookup_table()
        unvisited = {face.index for face in bm.faces}
        while unvisited:
            start = next(iter(unvisited))
            stack = [bm.faces[start]]
            comp_faces: list[bmesh.types.BMFace] = []
            while stack:
                face = stack.pop()
                if face.index not in unvisited:
                    continue
                unvisited.remove(face.index)
                comp_faces.append(face)
                for edge in face.edges:
                    for nface in edge.link_faces:
                        if nface.index in unvisited:
                            stack.append(nface)
            tris = sum(max(1, len(face.verts) - 2) for face in comp_faces)
            if tris > max_part_tris:
                continue
            verts = {vert for face in comp_faces for vert in face.verts}
            if not verts:
                continue
            world_verts = [obj.matrix_world @ vert.co for vert in verts]
            center = Vector(
                (
                    sum(v.x for v in world_verts) / len(world_verts),
                    sum(v.y for v in world_verts) / len(world_verts),
                    sum(v.z for v in world_verts) / len(world_verts),
                )
            )
            min_dist = min(_point_segment_distance(center, a, b) for a, b in segments)
            if min_dist >= min_distance_m:
                floating_count += 1
        bm.free()
    return floating_count


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


def _valid_unique_objects(objects: list[bpy.types.Object]) -> list[bpy.types.Object]:
    out: list[bpy.types.Object] = []
    seen: set[str] = set()
    for obj in objects:
        if obj is None or obj.name in seen or obj.name not in bpy.data.objects:
            continue
        seen.add(obj.name)
        out.append(obj)
    return out


def apply_uniform_scale(objects: list[bpy.types.Object], scale_factor: float, *, apply_transforms: bool) -> None:
    valid = _valid_unique_objects(objects)
    if not valid:
        return
    for obj in valid:
        obj.scale = obj.scale * float(scale_factor)
    if apply_transforms:
        apply_object_transforms(valid)


def translate_objects(
    objects: list[bpy.types.Object],
    delta_xyz: tuple[float, float, float],
    *,
    apply_transforms: bool,
) -> None:
    valid = _valid_unique_objects(objects)
    if not valid:
        return
    dx, dy, dz = float(delta_xyz[0]), float(delta_xyz[1]), float(delta_xyz[2])
    for obj in valid:
        obj.location.x += dx
        obj.location.y += dy
        obj.location.z += dz
    if apply_transforms:
        apply_object_transforms(valid)


def scale_group_to_height(
    *,
    measure_objects: list[bpy.types.Object],
    transform_objects: list[bpy.types.Object],
    target_height: float,
) -> float:
    measure = _valid_unique_objects(measure_objects)
    transforms = _valid_unique_objects(transform_objects)
    if not measure or not transforms:
        return 1.0
    current_h = model_height(measure)
    if current_h <= 1e-6:
        return 1.0
    scale_factor = target_height / current_h
    apply_uniform_scale(transforms, scale_factor, apply_transforms=True)
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


def move_group_to_ground_center(
    *,
    measure_objects: list[bpy.types.Object],
    transform_objects: list[bpy.types.Object],
) -> tuple[float, float, float]:
    measure = _valid_unique_objects(measure_objects)
    transforms = _valid_unique_objects(transform_objects)
    if not measure or not transforms:
        return (0.0, 0.0, 0.0)
    mins, maxs = bounds_min_max(measure)
    cx = (mins.x + maxs.x) * 0.5
    cy = (mins.y + maxs.y) * 0.5
    dz = -mins.z
    translate_objects(
        transforms,
        delta_xyz=(-cx, -cy, dz),
        apply_transforms=True,
    )
    return (-cx, -cy, dz)


def scale_objects_to_height(objects: list[bpy.types.Object], target_height: float) -> float:
    return scale_group_to_height(
        measure_objects=objects,
        transform_objects=objects,
        target_height=target_height,
    )


def move_objects_to_ground_center(objects: list[bpy.types.Object]) -> None:
    if not objects:
        return
    move_group_to_ground_center(
        measure_objects=objects,
        transform_objects=objects,
    )


def _is_unitish(value: float, eps: float = 1e-4) -> bool:
    return abs(float(value) - 1.0) <= eps


def armature_and_mesh_unit_scale_stats(
    *,
    armature_obj: bpy.types.Object | None,
    meshes: list[bpy.types.Object],
    eps: float = 1e-4,
) -> dict:
    valid_meshes = _valid_unique_objects(meshes)
    mesh_non_unit_objects = 0
    mesh_non_unit_axes = 0
    for mesh in valid_meshes:
        non_unit_axes = int(not _is_unitish(mesh.scale.x, eps)) + int(not _is_unitish(mesh.scale.y, eps)) + int(
            not _is_unitish(mesh.scale.z, eps)
        )
        if non_unit_axes > 0:
            mesh_non_unit_objects += 1
            mesh_non_unit_axes += non_unit_axes

    armature_present = bool(armature_obj is not None and object_exists(armature_obj))
    armature_non_unit_axes = 0
    if armature_present:
        armature_non_unit_axes = int(not _is_unitish(armature_obj.scale.x, eps)) + int(
            not _is_unitish(armature_obj.scale.y, eps)
        ) + int(not _is_unitish(armature_obj.scale.z, eps))

    return {
        "armature_present": armature_present,
        "armature_non_unit_axes": armature_non_unit_axes,
        "armature_unit_scale": armature_present and armature_non_unit_axes == 0,
        "mesh_non_unit_objects": mesh_non_unit_objects,
        "mesh_non_unit_axes": mesh_non_unit_axes,
        "mesh_unit_scale": mesh_non_unit_objects == 0,
    }


def _iter_unique_actions(actions: list[bpy.types.Action]) -> list[bpy.types.Action]:
    deduped: list[bpy.types.Action] = []
    seen: set[str] = set()
    for action in actions:
        if action is None or action.name in seen:
            continue
        seen.add(action.name)
        deduped.append(action)
    return deduped


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


def _is_scale_fcurve(data_path: str) -> bool:
    path = str(data_path or "")
    return path == "scale" or path.endswith(".scale")


def count_scale_keys(actions: list[bpy.types.Action]) -> dict:
    scale_key_count = 0
    scale_curve_count = 0
    actions_with_scale = 0
    for action in _iter_unique_actions(actions):
        touched = False
        for _, fcurve in _iter_action_fcurves(action):
            if not _is_scale_fcurve(fcurve.data_path):
                continue
            touched = True
            scale_curve_count += 1
            scale_key_count += len(fcurve.keyframe_points)
        if touched:
            actions_with_scale += 1
    return {
        "scale_key_count": scale_key_count,
        "scale_curve_count": scale_curve_count,
        "actions_with_scale_keys": actions_with_scale,
    }


def strip_bone_scale_fcurves(actions: list[bpy.types.Action]) -> dict:
    removed_curves = 0
    removed_keys = 0
    actions_touched = 0
    for action in _iter_unique_actions(actions):
        removable_by_owner: dict[int, tuple[object, list[bpy.types.FCurve]]] = {}
        for owner, fcurve in _iter_action_fcurves(action):
            if not _is_scale_fcurve(fcurve.data_path):
                continue
            owner_key = id(owner)
            if owner_key not in removable_by_owner:
                removable_by_owner[owner_key] = (owner, [])
            removable_by_owner[owner_key][1].append(fcurve)
        if not removable_by_owner:
            continue
        actions_touched += 1
        for owner, removable in removable_by_owner.values():
            for fcurve in removable:
                removed_curves += 1
                removed_keys += len(fcurve.keyframe_points)
                owner.remove(fcurve)
        if hasattr(action, "update_tag"):
            action.update_tag()
    return {
        "removed_scale_curves": removed_curves,
        "removed_scale_keys": removed_keys,
        "actions_touched": actions_touched,
    }


def force_pose_bone_unit_scale(armature_obj: bpy.types.Object | None) -> int:
    if armature_obj is None or not object_exists(armature_obj) or armature_obj.type != "ARMATURE":
        return 0
    if armature_obj.pose is None:
        return 0
    touched = 0
    for pbone in armature_obj.pose.bones:
        if (
            not _is_unitish(pbone.scale.x)
            or not _is_unitish(pbone.scale.y)
            or not _is_unitish(pbone.scale.z)
        ):
            touched += 1
        pbone.scale = (1.0, 1.0, 1.0)
    return touched


def _max_influences_and_weight_error(mesh_obj: bpy.types.Object) -> tuple[int, float]:
    if mesh_obj.type != "MESH":
        return 0, 0.0
    max_influences = 0
    max_error = 0.0
    for vertex in mesh_obj.data.vertices:
        weights = [float(group.weight) for group in vertex.groups if float(group.weight) > 0.0]
        if not weights:
            continue
        max_influences = max(max_influences, len(weights))
        max_error = max(max_error, abs(1.0 - sum(weights)))
    return max_influences, max_error


def _remove_empty_vertex_groups(mesh_obj: bpy.types.Object) -> int:
    if mesh_obj.type != "MESH":
        return 0
    used_names: set[str] = set()
    for vertex in mesh_obj.data.vertices:
        for group in vertex.groups:
            if group.weight <= 0.0:
                continue
            if group.group >= len(mesh_obj.vertex_groups):
                continue
            used_names.add(mesh_obj.vertex_groups[group.group].name)

    removed = 0
    for vgroup in list(mesh_obj.vertex_groups):
        if vgroup.name in used_names:
            continue
        mesh_obj.vertex_groups.remove(vgroup)
        removed += 1
    return removed


def _hard_limit_vertex_influences(mesh_obj: bpy.types.Object, max_influences: int) -> None:
    if mesh_obj.type != "MESH" or max_influences <= 0:
        return
    if len(mesh_obj.vertex_groups) == 0:
        return

    for vertex in mesh_obj.data.vertices:
        weighted: list[tuple[int, float]] = []
        for group in vertex.groups:
            if group.group >= len(mesh_obj.vertex_groups):
                continue
            w = float(group.weight)
            if w <= 0.0:
                continue
            weighted.append((group.group, w))
        if len(weighted) <= max_influences:
            continue

        weighted.sort(key=lambda item: item[1], reverse=True)
        keep = weighted[:max_influences]
        drop = weighted[max_influences:]
        keep_sum = sum(weight for _, weight in keep)
        if keep_sum <= 1e-8:
            continue

        for group_index, _ in drop:
            vgroup = mesh_obj.vertex_groups[group_index]
            vgroup.remove([vertex.index])
        for group_index, weight in keep:
            normalized = float(weight) / keep_sum
            vgroup = mesh_obj.vertex_groups[group_index]
            vgroup.add([vertex.index], normalized, "REPLACE")


def _smooth_weight_groups(mesh_obj: bpy.types.Object, *, aggressive: bool) -> None:
    target_markers = ("shoulder", "upperarm", "clav", "thigh", "hip", "pelvis")
    passes = 2 if aggressive else 1
    factor = 0.55 if aggressive else 0.35
    for _ in range(passes):
        for index, vgroup in enumerate(mesh_obj.vertex_groups):
            name = vgroup.name.lower()
            if not any(marker in name for marker in target_markers):
                continue
            mesh_obj.vertex_groups.active_index = index
            try:
                bpy.ops.object.vertex_group_smooth(
                    group_select_mode="ACTIVE",
                    factor=factor,
                    repeat=1,
                    expand=0.0,
                )
            except Exception:
                continue


def cleanup_skin_weights(
    *,
    meshes: list[bpy.types.Object],
    armature_obj: bpy.types.Object | None,
    max_influences: int = 4,
    tiny_weight: float = 0.0001,
    aggressive: bool = False,
) -> dict:
    candidates: list[bpy.types.Object] = []
    for mesh in _valid_unique_objects(meshes):
        if mesh.type != "MESH":
            continue
        if armature_obj is not None:
            if mesh_has_armature_modifier(mesh, armature_obj) or mesh_has_bone_group_match(mesh, armature_obj):
                candidates.append(mesh)
            continue
        if len(mesh.vertex_groups) > 0:
            candidates.append(mesh)

    max_before = 0
    max_after = 0
    err_before = 0.0
    err_after = 0.0
    empty_groups_removed = 0

    for mesh in candidates:
        current_max, current_err = _max_influences_and_weight_error(mesh)
        max_before = max(max_before, current_max)
        err_before = max(err_before, current_err)

        _activate_object(mesh)
        try:
            bpy.ops.object.mode_set(mode="WEIGHT_PAINT")
        except Exception:
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except Exception:
                pass

        for _ in range(2 if aggressive else 1):
            try:
                bpy.ops.object.vertex_group_limit_total(group_select_mode="ALL", limit=max_influences)
            except Exception:
                pass
            try:
                bpy.ops.object.vertex_group_clean(
                    group_select_mode="ALL",
                    limit=float(tiny_weight),
                    keep_single=True,
                )
            except Exception:
                pass
            try:
                bpy.ops.object.vertex_group_normalize_all(group_select_mode="ALL", lock_active=False)
            except Exception:
                pass
            _smooth_weight_groups(mesh, aggressive=aggressive)

        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except Exception:
            pass

        empty_groups_removed += _remove_empty_vertex_groups(mesh)
        _hard_limit_vertex_influences(mesh, max_influences=max_influences)
        current_max_after, current_err_after = _max_influences_and_weight_error(mesh)
        max_after = max(max_after, current_max_after)
        err_after = max(err_after, current_err_after)

    return {
        "skinned_meshes_processed": len(candidates),
        "max_vertex_influences_before": max_before,
        "max_vertex_influences_after": max_after,
        "weight_sum_error_max_before": err_before,
        "weight_sum_error_max_after": err_after,
        "empty_vertex_groups_removed": empty_groups_removed,
    }


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


def _find_root_bone_name(armature_obj: bpy.types.Object) -> str | None:
    if armature_obj.type != "ARMATURE" or armature_obj.data is None:
        return None
    bones = list(armature_obj.data.bones)
    if not bones:
        return None

    by_lower = {b.name.lower(): b.name for b in bones}
    preferred = [
        "root",
        "hips",
        "pelvis",
        "mixamorig:hips",
        "armature",
    ]
    for key in preferred:
        if key in by_lower:
            return by_lower[key]

    root_candidates = [b for b in bones if b.parent is None]
    if root_candidates:
        for bone in root_candidates:
            name = bone.name.lower()
            if "hip" in name or "pelvis" in name or "root" in name:
                return bone.name
        return root_candidates[0].name
    return bones[0].name


def _find_motion_bone_name(armature_obj: bpy.types.Object) -> str | None:
    if armature_obj.type != "ARMATURE" or armature_obj.data is None:
        return None
    bones = list(armature_obj.data.bones)
    if not bones:
        return None

    preferred_names: list[str] = []
    root = _find_root_bone_name(armature_obj)
    if root:
        preferred_names.append(root)
    preferred_names.extend([b.name for b in bones if b.name not in preferred_names])

    def _curve_hits(name: str) -> int:
        target = f'pose.bones["{name}"].location'
        hits = 0
        for action in bpy.data.actions:
            for _, fcurve in _iter_action_fcurves(action):
                if fcurve.data_path == target and fcurve.array_index in (0, 1):
                    hits += 1
        return hits

    best_name = preferred_names[0]
    best_hits = _curve_hits(best_name)
    for name in preferred_names[1:]:
        hits = _curve_hits(name)
        if hits > best_hits:
            best_name = name
            best_hits = hits
            if best_hits >= 2:
                break
    return best_name


def zero_root_motion_xy(armature_obj: bpy.types.Object) -> dict:
    root_bone = _find_motion_bone_name(armature_obj)
    if not root_bone:
        return {"root_bone": None, "actions_touched": 0, "max_xy_before": 0.0}

    target_paths = [
        f'pose.bones["{root_bone}"].location',
        "location",
    ]
    max_xy_before = 0.0
    actions_touched = 0

    for action in bpy.data.actions:
        touched = False
        for _, fcurve in _iter_action_fcurves(action):
            if fcurve.data_path not in target_paths or fcurve.array_index not in (0, 1):
                continue
            touched = True
            for point in fcurve.keyframe_points:
                current = float(point.co[1])
                max_xy_before = max(max_xy_before, abs(current))
                point.co[1] = 0.0
                point.handle_left[1] = 0.0
                point.handle_right[1] = 0.0
            fcurve.update()
        if touched:
            actions_touched += 1
            if hasattr(action, "update_tag"):
                action.update_tag()

    return {
        "root_bone": root_bone,
        "actions_touched": actions_touched,
        "max_xy_before": max_xy_before,
    }


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
