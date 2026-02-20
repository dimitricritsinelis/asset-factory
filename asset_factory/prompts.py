from __future__ import annotations

from textwrap import dedent

from .spec import AssetSpec


def make_image_prompt(spec: AssetSpec) -> str:
    return dedent(
        f"""
        Create a single clean concept image for a 3D game asset.
        Asset name: {spec.name}
        Kind: {spec.kind}
        Description: {spec.description}
        Style genre: {spec.style.genre}
        Style keywords: {", ".join(spec.style.keywords) if spec.style.keywords else "none"}
        Primary color: {spec.colors.primary}
        Secondary color: {spec.colors.secondary}

        Requirements:
        - Object centered on neutral background
        - No text overlays, no watermark, no logos
        - Keep silhouette clear for modeling reference
        """
    ).strip()


def make_character_sheet_prompt(spec: AssetSpec) -> str:
    return dedent(
        f"""
        Create a single 3-view character sheet image for 3D generation.
        Asset name: {spec.name}
        Description: {spec.description}
        Style genre: {spec.style.genre}
        Style keywords: {", ".join(spec.style.keywords) if spec.style.keywords else "none"}
        Primary color: {spec.colors.primary}
        Secondary color: {spec.colors.secondary}

        Composition requirements:
        - Three full-body views in one image: FRONT (left), SIDE (center), BACK (right)
        - Neutral light-gray background
        - Same character identity across all three views
        - A-pose, arms slightly away from body
        - No text labels, no watermark, no logos
        - Keep clean silhouette and readable proportions
        """
    ).strip()


def make_weapon_sheet_prompt(spec: AssetSpec) -> str:
    return dedent(
        f"""
        Create a single 3-view weapon sheet image for 3D generation.
        Asset name: {spec.name}
        Description: {spec.description}
        Style genre: {spec.style.genre}
        Style keywords: {", ".join(spec.style.keywords) if spec.style.keywords else "none"}
        Primary color: {spec.colors.primary}
        Secondary color: {spec.colors.secondary}

        Composition requirements:
        - Three consistent views in one image: FRONT (left), SIDE (center), TOP (right)
        - Neutral light-gray background
        - Centered object with even, shadow-soft lighting
        - No text labels, no watermark, no logos, no insignia
        - Preserve clear silhouette and proportion details
        """
    ).strip()


def make_tripo_prompt(spec: AssetSpec) -> str:
    return dedent(
        f"""
        High-fidelity competitive FPS character mesh (CS:GO-like quality target).
        Name: {spec.name}
        Kind: {spec.kind}
        Description: {spec.description}
        Style: {spec.style.genre}
        Keywords: {", ".join(spec.style.keywords) if spec.style.keywords else "none"}
        Target scale meters: {spec.scale_meters}
        Triangle budget target: {spec.tri_budget}
        Primary color: {spec.colors.primary}
        Secondary color: {spec.colors.secondary}
        Hard requirements:
        - coherent assembled single character, no exploded/floating disconnected parts
        - believable human proportions and readable silhouette
        - tactical gear forms clearly attached to body
        - clean game-ready topology and stable UVs
        - no logos, no readable text, no real insignia
        """
    ).strip()


def recipe_system_prompt() -> str:
    return dedent(
        """
        You generate strict Blender recipe JSON for procedural hard constraints.

        Never emit Python code.
        Never add fields outside schema.
        Output ONLY one JSON object with no markdown and no prose.
        Top-level keys must be exactly:
        version, name, units_scale_m, objects, materials, export, thumbnail
        Use ONLY these primitives:
        - cube, cylinder, uv_sphere, ico_sphere, plane

        Use ONLY these ops:
        - bevel(width, segments)
        - subdivide(levels)
        - decimate(ratio)
        - shade_smooth()
        - mirror(axis: X|Y|Z)
        - noise_displace(strength, scale)

        Keep topology clean and game-ready.
        Respect target_tris as a strict maximum after modifiers.
        Keep the recipe compact and deterministic.
        """
    ).strip()


def make_recipe_prompt(spec: AssetSpec, has_ref_image: bool) -> str:
    character_constraints = ""
    if spec.kind == "character":
        character_constraints = dedent(
            """
            Character structure requirements:
            - Build a multi-part character, not a single block.
            - Use at least 6 objects total.
            - Include distinct head, torso/chest, and pelvis/hip forms.
            - Include limb forms (arms and/or legs) using cylinders where appropriate.
            - Include at least one gear form (vest, backpack, hood, mask, pouch, or straps).
            - Prefer symmetry via mirror op on major body parts.
            - Avoid "single cube body" outputs.
            """
        ).strip()

    return dedent(
        f"""
        Generate one JSON object that matches the provided schema exactly.

        Asset spec:
        - name: {spec.name}
        - kind: {spec.kind}
        - description: {spec.description}
        - genre: {spec.style.genre}
        - keywords: {", ".join(spec.style.keywords) if spec.style.keywords else "none"}
        - scale_meters: {spec.scale_meters}
        - tri_budget: {spec.tri_budget}
        - texture.generate_basecolor: {spec.texture.generate_basecolor}
        - texture.basecolor_size: {spec.texture.basecolor_size}
        - colors.primary: {spec.colors.primary}
        - colors.secondary: {spec.colors.secondary}
        - seed: {spec.seed}

        Output constraints:
        - top-level keys must be exactly: version,name,units_scale_m,objects,materials,export,thumbnail
        - object keys must be exactly: name,primitive,location,rotation_deg,scale,ops,material
        - every op must use key "type" (not "op"), and its required fields
        - material keys must be exactly: name,basecolor
        - basecolor must be either:
          {{"mode":"solid","color_rgba":[r,g,b,a]}} OR {{"mode":"texture","texture_path":"..."}}
        - version must be "1"
        - export.format must be "glb"
        - export.apply_transforms must be true
        - export.triangulate must be true
        - export.uv_unwrap must be "smart" or "none"
        - export.target_tris must be <= {spec.tri_budget}
        - thumbnail.size must be 256
        - materials referenced by objects must exist
        {character_constraints}

        {"Reference image is attached; align major proportions and silhouette." if has_ref_image else "No reference image is attached; infer from text only."}
        """
    ).strip()


def make_recipe_repair_prompt(
    spec: AssetSpec,
    current_recipe_json: str,
    blender_error_log: str,
) -> str:
    return dedent(
        f"""
        Fix this recipe so Blender can execute it successfully while preserving the asset intent.
        Return a full corrected JSON recipe only.

        Asset name: {spec.name}
        Tri budget max: {spec.tri_budget}

        Current recipe JSON:
        {current_recipe_json}

        Blender failure log:
        {blender_error_log}
        """
    ).strip()


def make_character_recipe_repair_prompt(
    spec: AssetSpec,
    current_recipe_json: str,
    issues: list[str],
) -> str:
    issue_lines = "\n".join(f"- {issue}" for issue in issues)
    return dedent(
        f"""
        Repair this CHARACTER recipe to satisfy structure requirements.
        Return only corrected JSON recipe, no markdown.

        Asset name: {spec.name}
        Tri budget max: {spec.tri_budget}
        Required character structure:
        - at least 6 objects
        - distinct head object
        - distinct torso/chest object
        - distinct pelvis/hip object
        - at least 2 limb objects (arms/legs/hands/feet)
        - at least 1 gear object (vest/backpack/hood/mask/pouch/strap)
        - avoid all-cube-only composition

        Current issues:
        {issue_lines}

        Current recipe:
        {current_recipe_json}
        """
    ).strip()


def spec_system_prompt() -> str:
    return dedent(
        """
        You generate YAML-like asset specs as strict JSON objects for conversion to YAML.
        Output must match schema exactly.
        Keep values practical for procedural primitive-based modeling.
        """
    ).strip()


def make_new_spec_prompt(name: str, description: str) -> str:
    return dedent(
        f"""
        Create a valid asset spec for this request:
        - preferred name: {name}
        - request: {description}

        Constraints:
        - kind must be one of prop|environment|character
        - choose tri_budget suitable for small game-ready prop or simple character shell
        - include 2-5 style keywords
        - use hex-like color strings for primary/secondary
        - scale_meters should be realistic
        """
    ).strip()
