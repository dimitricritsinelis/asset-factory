# openai-asset-factory

YAML-first pipeline for generating game assets with:
- OpenAI (reference images + structured planning)
- Tripo3D (mesh generation)
- Blender (deterministic normalize/export/QC)

## What This Tool Is
- You edit YAML specs in `assets_src/specs/`.
- The pipeline builds GLBs, thumbnails, reports, and Blender files.
- Character builds can auto-build and embed a weapon from another spec.
- Offline mode is supported and always falls back to procedural output.

## Requirements
- Python 3.10+
- Blender installed locally
- Optional for online steps:
  - `OPENAI_API_KEY`
  - `TRIPO_API_KEY`

## Setup
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Set at least:
```env
BLENDER_BIN=/Applications/Blender.app/Contents/MacOS/Blender
OPENAI_API_KEY=
TRIPO_API_KEY=
```

## Quick Start
Offline smoke (no API calls):
```bash
make smoke
```

Build one asset:
```bash
make build NAME=stylized_crate
```

Build character with Tripo:
```bash
make build NAME=enemy_raider_01 ARGS="--provider tripo --force"
```

Strict mode (fail non-zero if degraded):
```bash
make build NAME=enemy_raider_01 ARGS="--provider tripo --force --strict"
```

Build all specs:
```bash
make build-all
```

## Asset Development Workflow
1. Create/edit spec in `assets_src/specs/<name>.yaml`.
2. Run offline first:
   - `make build NAME=<name> ARGS="--skip-openai --skip-images --force"`
3. Run online (if desired):
   - `make build NAME=<name> ARGS="--provider tripo --force"`
4. Inspect outputs:
   - `assets/models/<name>.glb`
   - `assets/reports/<name>.json`
   - `assets_src/blender/<name>.blend`
   - `assets/thumbnails/<name>.png`
5. Iterate spec (tri budget, style keywords, scale, colors, loadout attach offsets).

## Character + Weapon Embedding
For character spec:
```yaml
loadout:
  primary_weapon:
    spec: ak47
    embed_in_character_glb: true
    attach:
      socket_bone_name: weapon_socket_r
      bone_semantic: right_hand
    muzzle_socket_name: muzzle
```

Weapon runtime default path:
- `apps/client/public/assets/models/weapons/<weapon>/<weapon>.glb`

Character runtime path can be set with:
- `runtime_output_path: apps/client/public/assets/models/characters/<name>.glb`

## Build Outputs
For `<name>`:
- `assets/models/<name>.glb`
- `assets/models/<name>_lod1.glb` (character path)
- `assets/reports/<name>.json`
- `assets/thumbnails/<name>.png`
- `assets_src/blender/<name>.blend`
- `assets_src/logs/<name>.log`
- `assets_src/tripo_raw/<name>/...` (if Tripo used)

## Cleanup
Remove temporary test artifacts:
```bash
make clean-temp
```

## Troubleshooting
- Missing API key:
  - Set key in `.env`, or use `--skip-openai --skip-images`.
- Blender not found:
  - Set `BLENDER_BIN` to your Blender binary path.
- Build degraded:
  - Open `assets/reports/<name>.json` and read `status`, `reason/reasons`.

## See Also
- `AGENTS.md` (development guardrails)
- `DEVELOPMENT.md` (dev loop and validation)
- `SPEC_GUIDE.md` (spec schema and templates)
