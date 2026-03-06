from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from PIL import Image

from .config import Settings
from .plan_schema import OpenAITripoPlan


def _require_api_key(settings: Settings) -> str:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required for online builds.")
    return settings.openai_api_key


def _client(settings: Settings) -> Any:
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Missing dependency 'openai'. Install requirements and retry.") from exc
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
    raise RuntimeError("Unable to read OpenAI response text")


def generate_plan(*, system_prompt: str, user_prompt: str, settings: Settings) -> OpenAITripoPlan:
    response = _client(settings).responses.create(
        model=settings.openai_text_model,
        input=[
            {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
            {"role": "user", "content": [{"type": "input_text", "text": user_prompt}]},
        ],
        text={"format": {"type": "json_object"}},
    )
    payload = json.loads(_extract_output_text(response))
    return OpenAITripoPlan.model_validate(payload)


def generate_reference_sheet(prompt: str, out_path: Path, settings: Settings) -> None:
    response = _client(settings).images.generate(
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
    out_path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.open(io.BytesIO(image_bytes))
    image.save(out_path, format="PNG")


def split_reference_sheet(sheet_path: Path, front_path: Path, side_path: Path, back_path: Path) -> None:
    if not sheet_path.exists():
        raise FileNotFoundError(f"Reference sheet not found: {sheet_path}")
    image = Image.open(sheet_path).convert("RGBA")
    width, height = image.size
    third = width // 3
    if third <= 0:
        raise ValueError(f"Reference sheet width is too small to split: {sheet_path}")
    crops = [
        image.crop((0, 0, third, height)),
        image.crop((third, 0, third * 2, height)),
        image.crop((third * 2, 0, width, height)),
    ]
    for out in (front_path, side_path, back_path):
        out.parent.mkdir(parents=True, exist_ok=True)
    square_size = max(height, third)

    def _pad_to_square(src: Image.Image) -> Image.Image:
        canvas = Image.new("RGBA", (square_size, square_size), (235, 235, 235, 255))
        offset = ((square_size - src.width) // 2, (square_size - src.height) // 2)
        canvas.paste(src, offset)
        return canvas

    _pad_to_square(crops[0]).save(front_path, format="PNG")
    _pad_to_square(crops[1]).save(side_path, format="PNG")
    _pad_to_square(crops[2]).save(back_path, format="PNG")
