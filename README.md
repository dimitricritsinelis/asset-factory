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

Raider floating-artifact regression test (cached local inputs, no provider call):
```bash
make test-raider-cleanup
```

Raider movement regression test (in-place locomotion + clip activity QC):
```bash
make test-raider-movement
```

Raider ADS regression test (always-ADS two-hand hold + eye/sight geometric QC):
```bash
make test-raider-ads
```
This now includes a Blender-side output inspection gate (`assets_pipeline/tests/blender_ads_pose_qc.py`) that validates the generated `.blend` directly for:
- no layered NLA evaluation in review mode (`use_nla=false`)
- no non-unit pose-bone scales (deformation guard)
- multi-frame per-clip ADS thresholds (dense sampling with start/end coverage)
- `ads_eye_to_sight_m_max <= threshold`, `ads_eye_weapon_alignment_deg_max <= threshold`
- `ads_sight_alignment_deg_max` and `ads_wrist_delta_deg_max` (anti-flip wrist stability)
- catastrophic pose guards (elbow angle ranges, shoulder-hand span bounds, frame-to-frame hand jump limits)

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

Allow degraded output for an enforced character:
```bash
make build NAME=enemy_raider_01 ARGS="--provider tripo --force --allow-degraded-character"
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
      use_weapon_driven_ik: true
      marker_grip_right_name: WPN_GRIP_R
      marker_grip_left_name: WPN_GRIP_L
      marker_sight_name: WPN_SIGHT
      marker_muzzle_name: WPN_MUZZLE
      ads_enabled: true
      ads_clip_names: [idle_rifle_in_place, walk_rifle_in_place, run_rifle_in_place, strafe_left_rifle_in_place]
      grip_right_offset_m: [-0.034, 0.054, 0.25]
      grip_left_offset_m: [-0.05, 0.63, 0.21]
      sight_offset_m: [-0.10, 0.24, 0.52]
      # Optional world-space offset from head-bone position for ADS eye point.
      ads_eye_offset_m: [0.0, 0.0, 0.0]
      ads_aim_distance_m: 12.0
      ads_head_bone: Head
      ads_spine_bones: [Spine01, Spine02, R_Clavicle, NeckTwist01, NeckTwist02]
      qc_left_hand_grip_error_cm_max: 2.0
      qc_right_hand_grip_error_cm_max: 2.0
      qc_ads_eye_to_sight_m_max: 0.05
      qc_ads_eye_weapon_alignment_deg_max: 2.0
      qc_ads_sight_alignment_deg_max: 2.0
      qc_ads_wrist_delta_deg_max: 45.0
    muzzle_socket_name: muzzle
```

## Animation Preset Mapping
For character Tripo retargeting, map desired output clip names to explicit Tripo presets:
```yaml
generator:
  character_model_source: full_body
  tripo:
    animation_source: curated_then_tripo
    curated_animation_dir: assets_pipeline/animation_library/enemy_raider_01
    retarget_map_path: assets_pipeline/animation_library/enemy_raider_01/retarget_map.json
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
Clip request resolution is deterministic:
- If `generator.tripo.animations` and/or `generator.tripo.animation_presets` keys are present, only those output clip
  names are requested.
- Character defaults `idle`, `walk`, `run` are added only when no animation outputs are specified in the spec.
GLB animation export is Actions-only (`export_animation_mode="ACTIONS"`, `export_nla_strips=false`), so
`NlaTrack*` duplicates are not emitted.
When `generator.tripo.in_place: true`, normalize attempts to zero root XY translation in imported actions and records
`root_motion_zeroed`, `root_motion_max_xy`, and `root_motion_root_bone` in the report.

Additional report fields for character QC include:
- `bone_scale_keys_removed`
- `bone_scale_keys_remaining`
- `max_vertex_influences_before` / `max_vertex_influences_after`
- `weight_sum_error_max_before` / `weight_sum_error_max_after`
- `retargeted_clips`
- `ik_baked_actions`
- `hand_lock_error_cm_max`
- `ads_enabled`
- `ads_clips_targeted` / `ads_clips_baked`
- `ads_qc_failures`
- `left_hand_grip_error_cm_max`
- `right_hand_grip_error_cm_max`
- `head_aim_error_deg_max`
- `ads_eye_to_sight_m_max`
- `ads_eye_weapon_alignment_deg_max`
- `ads_sight_alignment_deg_max`
- `ads_wrist_delta_deg_max`
- `ads_qc_thresholds`
- `weapon_anchor_modes`
- `ads_clip_metrics`
- `requested_clips`
- `missing_clips`
- `floating_components_before` / `floating_components_after`
- `removed_loose_parts_by_size` / `removed_loose_parts_by_distance`
- `clip_motion_metrics`
- `movement_qc_failures`
- `locomotion_clips_checked`

## Rigged Character Normalize
- Rigged character imports use conservative loose-part cleanup plus distance-based floating shard removal.
- Character scale/ground normalization applies armature-safe transforms to prevent double-scaling skinned meshes.
- Normalize strips animated scale channels, forces pose-bone unit scale, and runs deterministic skin-weight cleanup.

## Curated Animation Contract
- Base directory: `assets_pipeline/animation_library/<character_name>/`
- Clip files: `clips/<clip_name>.glb|fbx|gltf`
- Optional retarget mapping: `retarget_map.json`

## Optional Hybrid Contract
- Spec field: `generator.character_model_source: base_human_hybrid`
- Base human contract path: `assets_pipeline/base_humans/<character_name>/base_human_rigged.glb`

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
