# openai-asset-factory

YAML-first pipeline for generating game assets with OpenAI + Tripo3D + Blender.

## Folder Intent
- `asset_factory/`: pipeline code
- `assets_pipeline/inputs/`: active YAML specs you want to build
- `assets_pipeline/examples/`: example specs (not built by `build-all`)
- `assets_pipeline/tests/`: test specs (used by smoke/offline checks)
- `assets_pipeline/{refs,recipes,blender,logs,tripo_raw}`: intermediate artifacts
- `assets/{models,reports,thumbnails}/production/`: production outputs
- `assets/{models,reports,thumbnails}/tests/`: test outputs

## Output Routing
- Specs from `assets_pipeline/inputs/` write to `assets/*/production/`.
- Specs from `assets_pipeline/tests/` write to `assets/*/tests/`.

## Requirements
- Python 3.10+
- Blender installed locally
- Optional online keys:
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

## Core Commands
Offline smoke (no API calls):
```bash
make smoke
```

Build active character:
```bash
make build NAME=enemy_raider_01
```

Build active weapon:
```bash
make build NAME=ak47
```

Build all active specs (`inputs/` only):
```bash
make build-all
```

Use Tripo explicitly:
```bash
make build NAME=enemy_raider_01 ARGS="--provider tripo --force"
```

Strict quality gate:
```bash
make build NAME=enemy_raider_01 ARGS="--provider tripo --force --strict"
```

## Spec Workflow
1. Edit/create YAML in `assets_pipeline/inputs/`.
2. Run offline first:
   - `make build NAME=<name> ARGS="--skip-openai --skip-images --force"`
3. Run online if needed:
   - `make build NAME=<name> ARGS="--provider tripo --force"`
4. Inspect:
   - `assets/models/production/<name>.glb`
   - `assets/reports/production/<name>.json`
   - `assets_pipeline/blender/<name>.blend`

## Character Weapon Embed
In a character spec:
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

## Animation Preset Mapping
For character Tripo retargeting, map desired output clip names to explicit Tripo presets:
```yaml
generator:
  tripo:
    animations:
      - idle_rifle_in_place
      - crouch_walk_rifle_in_place
      - death
    animation_presets:
      idle_rifle_in_place: idle
      crouch_walk_rifle_in_place: walk
      death: fall
```
Reports include `requested_clips`, `clip_names`, and `missing_clips`.

## Cleanup
```bash
make clean-temp
```

## Troubleshooting
- Missing keys: use `--skip-openai --skip-images` for offline fallback.
- Blender not found: set `BLENDER_BIN` in `.env`.
- Degraded result: inspect `assets/reports/*/<name>.json` (`status`, `reason/reasons`).

## Docs
- `AGENTS.md`
- `DEVELOPMENT.md`
- `SPEC_GUIDE.md`
