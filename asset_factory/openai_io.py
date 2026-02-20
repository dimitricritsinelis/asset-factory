from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from openai import OpenAI
from PIL import Image
from pydantic import ValidationError

from .config import Settings
from .recipe_schema import Recipe
from .spec import AssetSpec


def _require_api_key(settings: Settings) -> str:
    if not settings.openai_api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is required for online mode. "
            "Set it in .env, or run with --skip-openai --skip-images for offline mode."
        )
    return settings.openai_api_key


def _client(settings: Settings) -> OpenAI:
    return OpenAI(api_key=_require_api_key(settings))


def _extract_output_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if output_text:
        return str(output_text).strip()

    output = getattr(response, "output", None)
    if isinstance(output, list):
        chunks: list[str] = []
        for item in output:
            content = getattr(item, "content", None)
            if not isinstance(content, list):
                continue
            for c in content:
                text = getattr(c, "text", None)
                if isinstance(text, str) and text.strip():
                    chunks.append(text.strip())
        if chunks:
            return "\n".join(chunks)

    try:
        dumped = response.model_dump() if hasattr(response, "model_dump") else dict(response)
    except Exception as exc:
        raise RuntimeError("Unable to read OpenAI response text") from exc

    raise RuntimeError(f"No textual output in OpenAI response: {json.dumps(dumped)[:500]}")


def _maybe_add_image_content(content: list[dict[str, Any]], image_path: Path | None) -> None:
    if image_path is None or not image_path.exists():
        return
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    content.append({"type": "input_image", "image_url": f"data:image/png;base64,{encoded}"})


def _responses_json(
    *,
    settings: Settings,
    system_prompt: str,
    user_prompt: str,
    schema: dict[str, Any],
    schema_name: str,
    image_path: Path | None = None,
) -> dict[str, Any]:
    client = _client(settings)
    _ = schema
    _ = schema_name

    user_content: list[dict[str, Any]] = [{"type": "input_text", "text": user_prompt}]
    _maybe_add_image_content(user_content, image_path)

    response = client.responses.create(
        model=settings.openai_text_model,
        input=[
            {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
            {"role": "user", "content": user_content},
        ],
        text={
            "format": {
                "type": "json_object",
            }
        },
    )

    text = _extract_output_text(response)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Model output was not valid JSON: {text[:400]}") from exc


def generate_concept_image(prompt: str, out_path: Path, settings: Settings) -> None:
    client = _client(settings)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    response = client.images.generate(
        model=settings.openai_image_model,
        prompt=prompt,
        size="1024x1024",
    )

    data_item = response.data[0]
    if getattr(data_item, "b64_json", None):
        image_bytes = base64.b64decode(data_item.b64_json)
    elif getattr(data_item, "url", None):
        with urlopen(data_item.url, timeout=30) as handle:
            image_bytes = handle.read()
    else:
        raise RuntimeError("Image response did not include b64_json or url")

    image = Image.open(io.BytesIO(image_bytes))
    image.save(out_path, format="PNG")


def generate_character_sheet(prompt: str, out_path: Path, settings: Settings) -> None:
    # Keep square for broad model compatibility; prompt asks for 3-panel composition.
    generate_concept_image(prompt=prompt, out_path=out_path, settings=settings)


def split_character_sheet(
    sheet_path: Path,
    front_path: Path,
    side_path: Path,
    back_path: Path,
) -> None:
    if not sheet_path.exists():
        raise FileNotFoundError(f"Character sheet not found: {sheet_path}")

    image = Image.open(sheet_path).convert("RGBA")
    width, height = image.size
    third = width // 3
    if third <= 0:
        raise ValueError(f"Character sheet width is too small to split: {sheet_path}")

    crops = [
        image.crop((0, 0, third, height)),
        image.crop((third, 0, third * 2, height)),
        image.crop((third * 2, 0, width, height)),
    ]

    for out in (front_path, side_path, back_path):
        out.parent.mkdir(parents=True, exist_ok=True)

    # Tripo multiview behaves better with square panels; pad each crop to square.
    square_size = max(height, third)

    def _pad_to_square(src: Image.Image) -> Image.Image:
        canvas = Image.new("RGBA", (square_size, square_size), (235, 235, 235, 255))
        offset = ((square_size - src.width) // 2, (square_size - src.height) // 2)
        canvas.paste(src, offset)
        return canvas

    _pad_to_square(crops[0]).save(front_path, format="PNG")
    _pad_to_square(crops[1]).save(side_path, format="PNG")
    _pad_to_square(crops[2]).save(back_path, format="PNG")


def generate_weapon_sheet(prompt: str, out_path: Path, settings: Settings) -> None:
    generate_concept_image(prompt=prompt, out_path=out_path, settings=settings)


def split_weapon_sheet(
    sheet_path: Path,
    front_path: Path,
    side_path: Path,
    top_path: Path,
) -> None:
    # Same sheet geometry as character split; the right panel is interpreted as top view.
    split_character_sheet(sheet_path, front_path, side_path, top_path)


def generate_recipe(
    *,
    system_prompt: str,
    user_prompt: str,
    settings: Settings,
    image_path: Path | None,
) -> Recipe:
    prompt = user_prompt
    for attempt in range(1, 4):
        payload = _responses_json(
            settings=settings,
            system_prompt=system_prompt,
            user_prompt=prompt,
            schema=Recipe.model_json_schema(),
            schema_name="blender_recipe",
            image_path=image_path,
        )
        try:
            return Recipe.model_validate(payload)
        except ValidationError as exc:
            if attempt >= 3:
                raise
            # Feed the concrete validation errors back for deterministic repair.
            prompt = (
                f"{user_prompt}\n\n"
                "Your previous JSON was invalid for the required recipe schema.\n"
                "Fix it and return ONLY one JSON object with exact keys and no extras.\n"
                f"Validation errors:\n{exc}\n"
                f"Previous JSON:\n{json.dumps(payload, indent=2)}\n"
            )

    raise RuntimeError("Unreachable: recipe generation loop exited unexpectedly")


def generate_spec(
    *,
    system_prompt: str,
    user_prompt: str,
    settings: Settings,
) -> AssetSpec:
    payload = _responses_json(
        settings=settings,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        schema=AssetSpec.model_json_schema(),
        schema_name="asset_spec",
        image_path=None,
    )
    return AssetSpec.model_validate(payload)
