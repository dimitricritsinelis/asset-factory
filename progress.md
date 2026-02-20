# Progress

## Files touched
- `asset_factory/config.py`
- `asset_factory/spec.py`
- `asset_factory/cli.py`
- `asset_factory/build.py`
- `asset_factory/tripo_io.py`
- `asset_factory/blender_runner.py`
- `asset_factory/blender_ops.py`
- `asset_factory/prompts.py`
- `.env.example`
- `README.md`
- `assets_src/specs/ak47.yaml`
- `assets_src/specs/enemy_raider_01.yaml`

## What changed
- Added quality preset/settings env controls (`ASSET_FACTORY_QUALITY_PRESET`, `CHARACTER_HERO_TRIS`, `CHARACTER_LOD1_TRIS`, `CHARACTER_TEX_SIZE`).
- Added backward-compatible `quality_preset` to YAML schema.
- Added `generator.tripo.animations` and `generator.tripo.in_place` to spec schema.
- Added CLI `--strict` for `build` and `build-all`.
- Implemented character fail-safe orchestration with fallback chain:
  1) Tripo multiview+rig
  2) Tripo multiview no rig
  3) Tripo single-image (front)
  4) Tripo text-only
  5) Procedural mannequin fallback
- Build now attempts to always emit `assets/models/<name>.glb` and report; degraded outcomes are captured in report status/reasons.
- Added LOD1 path support (`assets/models/<name>_lod1.glb`) and normalize call wiring.
- Added Tripo animation stage:
  - requests clip retarget tasks per requested clip
  - downloads to `assets_src/tripo_raw/<name>/animations/<clip>.glb`
  - continues on per-clip failure and records degraded reasons
- Reworked Blender normalize mode to be character-safe:
  - no remesh path
  - armature-aware export mesh filtering
  - removes helper meshes/custom shapes/stray low-tri fragments
  - loose-part cleanup
  - selection-only export (mesh + armature)
  - normals + weighted normal
  - alpha/opacity fixes
  - optional backface culling disable for character mode
  - missing texture detection + fallback coloring
  - image packing into blend
  - LOD0 + LOD1 export
  - animation clip import/attach (NLA) for GLB export
- upgraded QC metrics (including clip metadata)
- Added weapon/loadout schema support (backward-compatible):
  - `runtime_output_path`
  - `loadout.primary_weapon` with `spec` and legacy aliases (`id`, `output_path`, `class`)
  - attach config (`socket_bone_name`, `bone_semantic`, offsets, rotation, scale) and `muzzle_socket_name`
- Added weapon pipeline orchestration in `build.py`:
  - `ensure_weapon_built(...)` with reuse -> Tripo weapon flow -> procedural AK-like fallback
  - runtime copy support to `apps/client/public/assets/models/...`
  - weapon sheet prompt + split refs (`front/side/top`)
  - character build now optionally builds weapon first and passes attach config to Blender normalize
- Added Blender normalize `weapon` mode:
  - rigid mesh join, opaque material cleanup, texture packing, scale-to-length, muzzle empty creation
  - selection-only export and expanded report fields (`weapon_*`, `muzzle_socket_name`, `reasons`)
- Character normalize now supports optional weapon embedding:
  - attaches weapon to socket bone on right hand when armature exists
  - fallback anchor embedding when armature is missing (degraded reason recorded)
  - preserves weapon mesh + muzzle empty in export selection
- `build.py` now lazily imports OpenAI/Tripo I/O modules to reduce startup coupling and keep offline paths lighter.
- README updated with weapon build flow and runtime path conventions.

## How to test
1. Validate CLI and imports:
```bash
.venv/bin/python -m asset_factory --help
.venv/bin/python -m py_compile asset_factory/*.py
```

2. Build weapon directly (online with keys):
```bash
make build NAME=ak47 ARGS="--provider tripo --force"
```

3. Build character + embedded weapon:
```bash
make build NAME=enemy_raider_01 ARGS="--provider tripo --force"
```

4. Offline fallback path (no OpenAI calls):
```bash
make build NAME=enemy_raider_01 ARGS="--provider tripo --skip-openai --skip-images --force"
```

5. Offline smoke (zero API calls):
```bash
make smoke
```

6. Character dry-run (no API calls):
```bash
.venv/bin/python -m asset_factory build enemy_raider_01 --provider tripo --dry-run
```

7. Online character build (requires keys):
```bash
make build NAME=enemy_raider_01 ARGS="--provider tripo --force"
```

8. Strict mode check:
```bash
make build NAME=enemy_raider_01 ARGS="--provider tripo --force --strict"
```

9. Animation verification:
```bash
cat assets/reports/enemy_raider_01.json
# inspect clip_names, clip_count, animations_present
```
