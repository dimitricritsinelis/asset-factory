from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, is_dataclass
import inspect
import json
from pathlib import Path
import shutil
from typing import Any
from urllib.parse import urlparse
from urllib.request import urlopen

from .config import Settings


@dataclass(frozen=True)
class TripoResult:
    task_payload: dict[str, Any]
    model_path: Path
    pbr_model_path: Path | None
    rendered_path: Path | None


def _require_tripo_key(settings: Settings) -> str:
    if not settings.tripo_api_key:
        raise RuntimeError("TRIPO_API_KEY is required for online builds.")
    return settings.tripo_api_key


def _build_client(settings: Settings) -> Any:
    try:
        from tripo3d import TripoClient
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Missing dependency 'tripo3d'. Install requirements and retry.") from exc
    api_key = _require_tripo_key(settings)
    try:
        return TripoClient(api_key=api_key)
    except TypeError:
        return TripoClient(api_key)


async def _await_maybe(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _to_plain(value: Any) -> Any:
    if is_dataclass(value):
        return {k: _to_plain(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {k: _to_plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_plain(v) for v in value]
    return value


def _task_to_dict(task_obj: Any) -> dict[str, Any]:
    if isinstance(task_obj, dict):
        return _to_plain(task_obj)
    if is_dataclass(task_obj):
        return _to_plain(asdict(task_obj))
    if hasattr(task_obj, "model_dump"):
        dumped = task_obj.model_dump()
        return dumped if isinstance(dumped, dict) else {"value": dumped}
    if hasattr(task_obj, "__dict__"):
        return _to_plain(dict(task_obj.__dict__))
    return {"value": str(task_obj)}


async def _wait_for_task(client: Any, task_id: str, timeout_s: int) -> dict[str, Any]:
    if hasattr(client, "wait_for_task"):
        task_obj = await _await_maybe(client.wait_for_task(task_id, timeout=float(timeout_s), verbose=False))
        task = _task_to_dict(task_obj)
    else:
        task = _task_to_dict(await _await_maybe(client.get_task(task_id)))
    status = str(task.get("status", "")).lower()
    if status in {"failed", "error"}:
        raise RuntimeError(f"Tripo task failed (task_id={task_id}): {json.dumps(task)[:1200]}")
    return task


def _collect_urls(node: Any, out: list[str]) -> None:
    if isinstance(node, str):
        parsed = urlparse(node)
        if parsed.scheme in {"http", "https", "file"}:
            out.append(node)
        return
    if isinstance(node, dict):
        for value in node.values():
            _collect_urls(value, out)
        return
    if isinstance(node, list):
        for value in node:
            _collect_urls(value, out)


def _download_url(url: str, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    parsed = urlparse(url)
    if parsed.scheme == "file":
        shutil.copyfile(Path(parsed.path), out_path)
        return out_path
    with urlopen(url, timeout=60) as handle:
        out_path.write_bytes(handle.read())
    return out_path


def _pick_outputs(task_payload: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    output = task_payload.get("output")
    if isinstance(output, dict):
        pbr = output.get("pbr_model")
        model = output.get("model") or output.get("base_model") or pbr
        rendered = output.get("rendered_image")
        return (
            pbr if isinstance(pbr, str) else None,
            model if isinstance(model, str) else None,
            rendered if isinstance(rendered, str) else None,
        )
    urls: list[str] = []
    _collect_urls(task_payload, urls)
    glb_urls = [u for u in urls if u.lower().endswith((".glb", ".gltf"))]
    png_urls = [u for u in urls if u.lower().endswith(".png")]
    pbr = next((u for u in glb_urls if "pbr" in u.lower()), None)
    model = next((u for u in glb_urls if u != pbr), None) if glb_urls else None
    if model is None:
        model = pbr
    rendered = png_urls[0] if png_urls else None
    return pbr, model, rendered


def _finalize_result(out_dir: Path, task_payload: dict[str, Any]) -> TripoResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "task.json").write_text(json.dumps(task_payload, indent=2), encoding="utf-8")
    pbr_url, model_url, render_url = _pick_outputs(task_payload)
    if not model_url:
        raise RuntimeError(
            "Tripo task completed but no downloadable GLB URL was found in task payload. "
            f"See {out_dir / 'task.json'}"
        )
    model_path = _download_url(model_url, out_dir / "model.glb")
    pbr_model_path = _download_url(pbr_url, out_dir / "pbr.glb") if pbr_url else None
    rendered_path = _download_url(render_url, out_dir / "rendered.png") if render_url else None
    return TripoResult(
        task_payload=task_payload,
        model_path=model_path,
        pbr_model_path=pbr_model_path,
        rendered_path=rendered_path,
    )


def generate_model_from_images(
    *,
    images: list[str],
    prompt: str,
    out_dir: Path,
    settings: Settings,
    face_limit: int,
    model_version: str,
    texture_quality: str,
) -> TripoResult:
    if not images:
        raise ValueError("At least one image is required for Tripo generation")

    async def _run() -> TripoResult:
        client = _build_client(settings)
        normalized = [str(Path(image).resolve()) if Path(image).exists() else image for image in images]
        try:
            task_id = await _await_maybe(
                client.multiview_to_model(
                    images=normalized,
                    face_limit=face_limit,
                    model_version=model_version,
                    texture_quality=texture_quality,
                    pbr=True,
                    texture=True,
                )
            )
        except TypeError:
            task_id = await _await_maybe(client.multiview_to_model(images=normalized, model_version=model_version))
        if not isinstance(task_id, str):
            raise RuntimeError(f"Unexpected Tripo task identifier: {task_id!r}")
        task_payload = await _wait_for_task(client, task_id, settings.tripo_timeout_s)
        if hasattr(client, "texture_model"):
            try:
                texture_task_id = await _await_maybe(
                    client.texture_model(
                        task_id,
                        text_prompt=prompt,
                        pbr=True,
                        texture=True,
                        texture_quality=texture_quality,
                        model_version=model_version,
                    )
                )
            except Exception:
                texture_task_id = None
            if isinstance(texture_task_id, str):
                task_payload = await _wait_for_task(client, texture_task_id, settings.tripo_timeout_s)
        return _finalize_result(out_dir, task_payload)

    return asyncio.run(_run())
