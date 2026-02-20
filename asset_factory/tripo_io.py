from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, is_dataclass
import json
from pathlib import Path
import shutil
import inspect
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


@dataclass(frozen=True)
class TripoAnimationResult:
    clip_name: str
    path: Path | None
    error: str | None


def _require_tripo_key(settings: Settings) -> str:
    if not settings.tripo_api_key:
        raise RuntimeError(
            "TRIPO_API_KEY is required for provider=tripo. "
            "Set it in .env or use --provider procedural."
        )
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
    if hasattr(value, "value") and isinstance(getattr(value, "value"), str):
        return getattr(value, "value")
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
        try:
            task_obj = await _await_maybe(
                client.wait_for_task(task_id, timeout=float(timeout_s), verbose=False)
            )
        except ModuleNotFoundError as exc:
            if exc.name == "aiohttp":
                raise RuntimeError(
                    "Missing dependency 'aiohttp' required by tripo3d task polling. "
                    "Install requirements again: .venv/bin/pip install -r requirements.txt"
                ) from exc
            raise
        task = _task_to_dict(task_obj)
        status = str(task.get("status", "")).lower()
        if status in {"failed", "error"}:
            raise RuntimeError(f"Tripo task failed (task_id={task_id}): {json.dumps(task)[:1200]}")
        return task

    # Fallback for alternate SDKs.
    task_obj = await _await_maybe(client.get_task(task_id))
    return _task_to_dict(task_obj)


def _collect_urls(node: Any, out: list[str]) -> None:
    if isinstance(node, str):
        parsed = urlparse(node)
        if parsed.scheme in {"http", "https", "file"}:
            out.append(node)
        return
    if isinstance(node, dict):
        for v in node.values():
            _collect_urls(v, out)
        return
    if isinstance(node, list):
        for v in node:
            _collect_urls(v, out)


def _download_url(url: str, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    parsed = urlparse(url)

    if parsed.scheme == "file":
        src = Path(parsed.path)
        shutil.copyfile(src, out_path)
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


def _save_task_json(out_dir: Path, payload: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "task.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _save_named_task_json(out_path: Path, payload: dict[str, Any]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _finalize_result(client: Any, out_dir: Path, task_payload: dict[str, Any]) -> TripoResult:
    _save_task_json(out_dir, task_payload)

    pbr_url, model_url, render_url = _pick_outputs(task_payload)

    # Prefer official SDK download helper when available.
    if hasattr(client, "download_task_models") and (model_url is None and pbr_url is None):
        try:
            task_id = task_payload.get("task_id")
            if isinstance(task_id, str):
                task_obj = client.get_task(task_id)
                downloads = client.download_task_models(task_obj, str(out_dir))
                model_url = downloads.get("model")
                pbr_url = downloads.get("pbr_model")
        except Exception:
            pass

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


async def _run_rig_if_needed(
    client: Any, *, enabled: bool, model_task: dict[str, Any], timeout_s: int
) -> dict[str, Any]:
    if not enabled:
        return model_task

    task_id = model_task.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        return model_task

    rig_task_id = await _await_maybe(client.rig_model(task_id, rig_type="biped"))
    if not isinstance(rig_task_id, str):
        return model_task
    return await _wait_for_task(client, rig_task_id, timeout_s)


async def _apply_optional_texture_prompt(
    client: Any,
    *,
    task_payload: dict[str, Any],
    prompt: str,
    timeout_s: int,
    pbr: bool,
    texture: bool,
    texture_quality: str,
    model_version: str,
) -> dict[str, Any]:
    task_id = task_payload.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        return task_payload
    if not hasattr(client, "texture_model"):
        return task_payload

    try:
        tex_task_id = await _await_maybe(
            client.texture_model(
                task_id,
                text_prompt=prompt,
                pbr=pbr,
                texture=texture,
                texture_quality=texture_quality,
                model_version=model_version,
            )
        )
    except TypeError:
        return task_payload
    except Exception:
        return task_payload

    if not isinstance(tex_task_id, str):
        return task_payload
    return await _wait_for_task(client, tex_task_id, timeout_s)


def _normalize_image_inputs(images: list[str]) -> list[str]:
    normalized: list[str] = []
    for image in images:
        p = Path(image)
        normalized.append(str(p.resolve()) if p.exists() else image)
    if not normalized:
        raise ValueError("At least one image is required for image/multiview generation")
    return normalized


def _is_invalid_parameter_error(exc: Exception) -> bool:
    code = getattr(exc, "code", None)
    if code == 1004:
        return True
    text = str(exc).lower()
    return "invalid" in text and "parameter" in text


async def _close_client(client: Any) -> None:
    close_fn = getattr(client, "close", None)
    if close_fn is None:
        return
    try:
        await _await_maybe(close_fn())
    except Exception:
        pass


async def _create_multiview_task(
    client: Any,
    *,
    images: list[str],
    face_limit: int,
    model_version: str,
    texture_quality: str,
    pbr: bool,
    texture: bool,
) -> tuple[str, str]:
    # Retry with progressively simpler parameter sets to avoid SDK/API mismatches.
    param_attempts: list[tuple[str, dict[str, Any]]] = [
        (
            "full",
            {
                "images": images,
                "face_limit": face_limit,
                "model_version": model_version,
                "texture_quality": texture_quality,
                "pbr": pbr,
                "texture": texture,
            },
        ),
        ("reduced", {"images": images, "model_version": model_version, "pbr": pbr, "texture": texture}),
        ("minimal", {"images": images, "model_version": model_version}),
    ]
    errors: list[str] = []
    for label, kwargs in param_attempts:
        try:
            task_id = await _await_maybe(client.multiview_to_model(**kwargs))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{label}: {exc}")
            if not _is_invalid_parameter_error(exc):
                raise
            continue
        if isinstance(task_id, str):
            return task_id, label
        errors.append(f"{label}: unexpected return type {type(task_id)}")
    raise RuntimeError("multiview_to_model failed: " + " | ".join(errors))


async def _create_image_task(
    client: Any,
    *,
    image: str,
    face_limit: int,
    model_version: str,
    texture_quality: str,
    pbr: bool,
    texture: bool,
) -> tuple[str, str]:
    param_attempts: list[tuple[str, dict[str, Any]]] = [
        (
            "full",
            {
                "image": image,
                "face_limit": face_limit,
                "model_version": model_version,
                "texture_quality": texture_quality,
                "pbr": pbr,
                "texture": texture,
            },
        ),
        ("reduced", {"image": image, "model_version": model_version, "pbr": pbr, "texture": texture}),
        ("minimal", {"image": image, "model_version": model_version}),
    ]
    errors: list[str] = []
    for label, kwargs in param_attempts:
        try:
            task_id = await _await_maybe(client.image_to_model(**kwargs))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{label}: {exc}")
            if not _is_invalid_parameter_error(exc):
                raise
            continue
        if isinstance(task_id, str):
            return task_id, label
        errors.append(f"{label}: unexpected return type {type(task_id)}")
    raise RuntimeError("image_to_model failed: " + " | ".join(errors))


def _multiview_candidates(image_inputs: list[str]) -> list[tuple[str, list[str]]]:
    candidates: list[tuple[str, list[str]]] = [("as_is", image_inputs)]
    if len(image_inputs) == 3:
        # Some endpoints expect four views; duplicate side view as a stable fallback.
        candidates.append(("three_plus_side_dup", [image_inputs[0], image_inputs[1], image_inputs[2], image_inputs[1]]))
    if len(image_inputs) > 4:
        candidates.append(("first_four", image_inputs[:4]))

    deduped: list[tuple[str, list[str]]] = []
    seen: set[tuple[str, ...]] = set()
    for label, images in candidates:
        key = tuple(images)
        if key in seen:
            continue
        seen.add(key)
        deduped.append((label, images))
    return deduped


def _normalize_clip_name(raw: str) -> str:
    clip = raw.strip().lower().replace(" ", "_")
    aliases = {
        "idle": "idle",
        "walk": "walk",
        "run": "run",
        "shoot": "shoot",
        "turn": "turn",
        "jump": "jump",
        "hurt": "hurt",
        "fall": "fall",
        "dive": "dive",
        "climb": "climb",
        "slash": "slash",
    }
    if clip in aliases:
        return aliases[clip]
    if clip.startswith("preset:"):
        return clip.split("preset:", 1)[1].replace(":", "_")
    return clip


def _clip_animation_arg(raw: str) -> str:
    clip = raw.strip().lower().replace(" ", "_")
    if clip.startswith("preset:"):
        return clip
    if clip.endswith("_in_place"):
        clip = clip[: -len("_in_place")]
    aliases = {
        "walk_cycle": "walk",
        "run_cycle": "run",
        "idle_loop": "idle",
        "death": "fall",
        "strafe_left": "walk",
        "strafe_right": "walk",
    }
    clip = aliases.get(clip, clip)
    return f"preset:{clip.replace(' ', '_')}"


def generate_model_from_text(
    *,
    prompt: str,
    out_dir: Path,
    settings: Settings,
    face_limit: int,
    model_version: str,
    texture_quality: str,
    pbr: bool,
    texture: bool,
    rig: bool,
) -> TripoResult:
    async def _run() -> TripoResult:
        client = _build_client(settings)
        try:
            task_id = await _await_maybe(
                client.text_to_model(
                    prompt=prompt,
                    face_limit=face_limit,
                    model_version=model_version,
                    texture_quality=texture_quality,
                    pbr=pbr,
                    texture=texture,
                )
            )
            if not isinstance(task_id, str):
                raise RuntimeError(f"Unexpected text_to_model return type: {type(task_id)}")

            task = await _wait_for_task(client, task_id, settings.tripo_timeout_s)
            task = await _run_rig_if_needed(
                client, enabled=rig, model_task=task, timeout_s=settings.tripo_timeout_s
            )
            return _finalize_result(client, out_dir, task)
        finally:
            await _close_client(client)

    return asyncio.run(_run())


def generate_model_from_images(
    *,
    images: list[str],
    prompt: str,
    out_dir: Path,
    settings: Settings,
    face_limit: int,
    model_version: str,
    texture_quality: str,
    pbr: bool,
    texture: bool,
    rig: bool,
) -> TripoResult:
    async def _run() -> TripoResult:
        client = _build_client(settings)
        try:
            image_inputs = _normalize_image_inputs(images)
            task_id: str | None = None
            multiview_errors: list[str] = []

            if len(image_inputs) >= 2:
                for variant_name, variant_images in _multiview_candidates(image_inputs):
                    try:
                        task_id, param_variant = await _create_multiview_task(
                            client,
                            images=variant_images,
                            face_limit=face_limit,
                            model_version=model_version,
                            texture_quality=texture_quality,
                            pbr=pbr,
                            texture=texture,
                        )
                        break
                    except Exception as exc:  # noqa: BLE001
                        multiview_errors.append(f"{variant_name}: {exc}")

            if task_id is None:
                try:
                    task_id, _ = await _create_image_task(
                        client,
                        image=image_inputs[0],
                        face_limit=face_limit,
                        model_version=model_version,
                        texture_quality=texture_quality,
                        pbr=pbr,
                        texture=texture,
                    )
                except Exception as exc:
                    prefix = " | ".join(multiview_errors) if multiview_errors else "no multiview attempts"
                    raise RuntimeError(
                        "Tripo image generation failed. "
                        f"Multiview errors: {prefix}. "
                        f"Image fallback error: {exc}"
                    ) from exc

            task = await _wait_for_task(client, task_id, settings.tripo_timeout_s)
            task = await _apply_optional_texture_prompt(
                client,
                task_payload=task,
                prompt=prompt,
                timeout_s=settings.tripo_timeout_s,
                pbr=pbr,
                texture=texture,
                texture_quality=texture_quality,
                model_version=model_version,
            )
            task = await _run_rig_if_needed(
                client, enabled=rig, model_task=task, timeout_s=settings.tripo_timeout_s
            )
            return _finalize_result(client, out_dir, task)
        finally:
            await _close_client(client)

    return asyncio.run(_run())


def generate_animation_clips(
    *,
    source_task_payload: dict[str, Any],
    clip_names: list[str],
    in_place: bool,
    out_dir: Path,
    settings: Settings,
) -> list[TripoAnimationResult]:
    task_id = source_task_payload.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        raise RuntimeError("Cannot generate animations: source task payload does not include task_id")

    normalized = [_normalize_clip_name(c) for c in clip_names if c and c.strip()]
    if not normalized:
        return []

    async def _run() -> list[TripoAnimationResult]:
        client = _build_client(settings)
        results: list[TripoAnimationResult] = []
        try:
            anim_dir = out_dir / "animations"
            anim_dir.mkdir(parents=True, exist_ok=True)
            for clip in normalized:
                try:
                    animation_arg = _clip_animation_arg(clip)
                    retarget_task_id = await _await_maybe(
                        client.retarget_animation(
                            original_model_task_id=task_id,
                            animation=animation_arg,
                            out_format="glb",
                            bake_animation=True,
                            export_with_geometry=True,
                            animate_in_place=in_place,
                        )
                    )
                    if not isinstance(retarget_task_id, str):
                        results.append(
                            TripoAnimationResult(
                                clip_name=clip,
                                path=None,
                                error=f"unexpected retarget task id type: {type(retarget_task_id)}",
                            )
                        )
                        continue

                    task_payload = await _wait_for_task(client, retarget_task_id, settings.tripo_timeout_s)
                    _save_named_task_json(anim_dir / f"{clip}_task.json", task_payload)
                    _, model_url, _ = _pick_outputs(task_payload)
                    if not model_url:
                        results.append(
                            TripoAnimationResult(
                                clip_name=clip,
                                path=None,
                                error=f"no model URL in task output for clip '{clip}'",
                            )
                        )
                        continue
                    clip_path = _download_url(model_url, anim_dir / f"{clip}.glb")
                    results.append(TripoAnimationResult(clip_name=clip, path=clip_path, error=None))
                except Exception as exc:  # noqa: BLE001
                    results.append(TripoAnimationResult(clip_name=clip, path=None, error=str(exc)))
            return results
        finally:
            await _close_client(client)

    return asyncio.run(_run())
