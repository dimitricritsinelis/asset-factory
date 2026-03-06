# AGENTS.md

Single source of truth for this repo.

This is the only documentation file that should define repo behavior.

## Mission

Build one asset only:

`asset_definitions/enemy_raider.yaml -> OpenAI plan + OpenAI 2D reference sheet -> Tripo 2D-to-3D conversion -> generated_asset_files/final_3d_models/*/enemy_raider.glb + generated_asset_files/build_reports/*/enemy_raider.json`

## Rules

- Keep the repo single-path.
- `enemy_raider` is the only active asset.
- Keep the provider flow fixed: OpenAI planning -> OpenAI 2D references -> Tripo 3D conversion.
- Do not add alternate assets, alternate providers, fallback generators, or extra build branches.
- Do not commit secrets.
- Do not remove offline fixture replay.
- Do not break `make smoke`.
- Every build must attempt to write:
  - `generated_asset_files/final_3d_models/*/enemy_raider.glb`
  - `generated_asset_files/build_reports/*/enemy_raider.json`
- Failures or degraded builds must be explicit in the JSON report.

## Minimal Structure

```text
AGENTS.md                                               Repo instructions and documentation
Makefile                                                Main commands
.env.example                                            Required environment variables
requirements.txt                                        Python dependencies
asset_definitions/enemy_raider.yaml                     Active asset definition
asset_builder_code/asset_factory/                       Entire Python implementation
tests/fixtures/                                         Offline smoke fixture
tests/test_online_build.py                              Mocked online-path test
generated_asset_files/openai_logs/enemy_raider/         OpenAI plan and prompt logs
generated_asset_files/openai_2d_reference_images/enemy_raider/ OpenAI-generated 2D sheet and split views
generated_asset_files/tripo_3d_raw_outputs/enemy_raider/ Tripo raw 3D payloads and downloads
generated_asset_files/final_3d_models/                  Final GLB outputs by tier
generated_asset_files/build_reports/                    Final JSON reports by tier
```

## Commands

```bash
make setup
make build SPEC=asset_definitions/enemy_raider.yaml
make smoke
make check
```

Direct CLI:

```bash
PYTHONPATH=asset_builder_code python3 -m asset_factory build asset_definitions/enemy_raider.yaml
PYTHONPATH=asset_builder_code python3 -m asset_factory build asset_definitions/enemy_raider.yaml --fixture-replay --force
```

## Environment

Copy `.env.example` to `.env` for online builds.

Required:

```env
OPENAI_API_KEY=
TRIPO_API_KEY=
```

Optional:

```env
OPENAI_TEXT_MODEL=gpt-5-mini
OPENAI_IMAGE_MODEL=gpt-image-1.5
TRIPO_MODEL_VERSION=v2.5-20250123
TRIPO_TEXTURE_QUALITY=detailed
TRIPO_TIMEOUT_S=900
```

## Asset Definition

The only supported asset definition is `asset_definitions/enemy_raider.yaml`.

```yaml
name: enemy_raider
description: Hostile raider NPC for FPS.
style:
  genre: realistic-competitive-fps
  keywords:
    - competitive shooter readability
    - hooded mask
scale_meters: 1.78
tri_budget: 40000
colors:
  primary: "#C2A97A"
  secondary: "#3F4A38"
seed: 81927
```

Field rules:

- `name` must remain `enemy_raider`
- `description` is the primary asset brief
- `style.genre` and `style.keywords` shape OpenAI planning
- `scale_meters` and `tri_budget` feed planning and reporting
- `colors` guide visual direction
- `seed` is optional reproducibility metadata

## Fixed Pipeline

1. Load `asset_definitions/enemy_raider.yaml`
2. Ask OpenAI for a structured JSON plan containing:
   - `reference_sheet_prompt`
   - `tripo_prompt`
   - `notes`
3. Generate one three-view reference sheet with OpenAI Images
4. Split the sheet into front / side / back images
5. Send those images plus the prompt to Tripo
6. Copy the resulting model to the correct output tier
7. Write a JSON report

## Offline Smoke

- Fixture source: `tests/fixtures/`
- `make smoke` replays committed fixture files with no network access
- Smoke writes:
  - `generated_asset_files/final_3d_models/tests/enemy_raider.glb`
  - `generated_asset_files/build_reports/tests/enemy_raider.json`
- Fixture files must never be mutated during smoke; they are staged into generated working folders first

## Reports

Reports must contain:

- `status`
- `asset_name`
- degraded `reason` / `reasons` when applicable
- `source_mode`
- asset definition path
- `spec_sha256`
- OpenAI plan path
- reference image paths
- Tripo task/raw model paths
- final export path

Reports must only describe the current static asset build and its generated artifacts.

## Baseline

- Production model: `generated_asset_files/final_3d_models/production/enemy_raider.glb`
- Production report: `generated_asset_files/build_reports/production/enemy_raider.json`

## Quality Checklist

1. `PYTHONPATH=asset_builder_code python3 -m py_compile asset_builder_code/asset_factory/*.py`
2. `PYTHONPATH=asset_builder_code python3 -m asset_factory --help`
3. `PYTHONPATH=asset_builder_code python3 -m unittest tests/test_online_build.py`
4. `make smoke`
5. Keep this `AGENTS.md` current when behavior changes

## Safety

- Never run destructive git commands.
- Never mutate committed fixture inputs during smoke.
- Keep this file aligned with the actual repo behavior.
- Do not add separate README/spec/docs files that duplicate this contract.
