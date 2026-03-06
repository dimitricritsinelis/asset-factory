from __future__ import annotations

from textwrap import dedent

from .spec import AssetSpec


def plan_system_prompt() -> str:
    return dedent(
        """
        You produce strict JSON for the enemy_raider 3D asset generation pipeline.

        Return exactly one JSON object with these keys:
        - version
        - asset_name
        - reference_sheet_prompt
        - tripo_prompt
        - notes

        Rules:
        - asset_name must be enemy_raider
        - reference_sheet_prompt must request one three-view character sheet
        - FRONT on the left, SIDE in the center, BACK on the right
        - no text, no watermark, no logos
        - tripo_prompt must describe one coherent static enemy raider character mesh
        - notes must be a short list of implementation reminders
        """
    ).strip()


def make_plan_prompt(spec: AssetSpec) -> str:
    return dedent(
        f"""
        Create a structured plan for building the single supported static 3D asset in this repo.

        Asset name: {spec.name}
        Description: {spec.description}
        Style genre: {spec.style.genre}
        Style keywords: {", ".join(spec.style.keywords) if spec.style.keywords else "none"}
        Scale meters: {spec.scale_meters}
        Triangle budget: {spec.tri_budget}
        Primary color: {spec.colors.primary}
        Secondary color: {spec.colors.secondary}

        The output must be suitable for:
        1. generating one OpenAI reference sheet with front, side, and back views
        2. generating one Tripo static 3D mesh from those views
        3. producing a hostile raider NPC silhouette that reads clearly in a realistic competitive FPS
        """
    ).strip()
