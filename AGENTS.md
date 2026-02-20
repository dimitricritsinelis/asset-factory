# AGENTS.md

This file defines repository rules for human and AI contributors.

## Mission
Build reliable, reproducible 3D asset outputs from YAML specs with strict fallbacks.

## Non-Negotiables
- Do not commit secrets.
- Do not remove offline mode.
- Do not break `make smoke`.
- `build` must always try to produce:
  - `assets/models/<name>.glb`
  - `assets/reports/<name>.json`
- If generation degrades, report it explicitly in QC JSON.

## Providers
- OpenAI: reference images + structured planning only.
- Tripo3D: mesh generation.
- Blender: deterministic normalization/export.
- Do not add other LLM/image providers.

## Command and Path Discipline
- Use commands already present in `Makefile` when possible.
- Do not invent path conventions; follow:
  - Source specs: `assets_pipeline/inputs/*.yaml`
  - Build outputs: `assets/models`, `assets/reports`, `assets/thumbnails`
  - Runtime targets: `apps/client/public/assets/models/...` (when configured)

## Code Rules
- Prefer typed Python and small functions.
- Keep schema validation strict and backward-compatible.
- Fail with actionable errors.
- Preserve fallback chains and degraded reporting.

## Quality Checklist Before Finishing
1. `python -m py_compile asset_factory/*.py`
2. `python -m asset_factory --help`
3. `make smoke` (or explain why not run)
4. Confirm README/docs updated for any behavior changes.

## Safety
- Never run destructive git commands.
- Never delete user assets unless explicitly requested.
