# SPEC_GUIDE.md

## Where Specs Go
- Active specs: `assets_pipeline/inputs/`
- Example specs: `assets_pipeline/examples/`
- Test specs: `assets_pipeline/tests/`

## Minimal Active Spec
```yaml
name: enemy_raider_01
kind: character
scale_meters: 1.78
tri_budget: 25000
texture:
  generate_basecolor: false
  basecolor_size: 1024
```

## Character With Weapon Embed
```yaml
name: enemy_raider_01
kind: character
runtime_output_path: apps/client/public/assets/models/characters/enemy_raider_01.glb

generator:
  character_model_source: full_body
  provider: tripo
  tripo:
    mode: multiview
    rig: true
    animation_source: curated_then_tripo
    curated_animation_dir: assets_pipeline/animation_library/enemy_raider_01
    retarget_map_path: assets_pipeline/animation_library/enemy_raider_01/retarget_map.json
    animations: [idle, walk, run]
    animation_presets:
      idle_rifle_in_place: idle
      walk_rifle_in_place: walk
      run_rifle_in_place: run
      crouch_idle_rifle: idle
      crouch_walk_rifle_in_place: walk
      shoot_rifle_upper: shoot
      death: fall
    in_place: true

loadout:
  primary_weapon:
    spec: ak47
    embed_in_character_glb: true
    attach:
      socket_bone_name: weapon_socket_r
      bone_semantic: right_hand
      offset_m: [0.0, 0.0, 0.0]
      rotation_deg: [0.0, 0.0, 0.0]
      scale: 1.0
      ads_enabled: true
      ads_clip_names: [idle_rifle_in_place, walk_rifle_in_place, run_rifle_in_place, strafe_left_rifle_in_place]
      grip_right_offset_m: [-0.034, 0.054, 0.25]
      grip_left_offset_m: [-0.05, 0.63, 0.21]
      sight_offset_m: [-0.10, 0.24, 0.52]
      ads_eye_offset_m: [0.0, 0.0, 0.0]
      ads_aim_distance_m: 12.0
      ads_head_bone: Head
      ads_spine_bones: [Spine01, Spine02, R_Clavicle, NeckTwist01, NeckTwist02]
    muzzle_socket_name: muzzle

character_qc_enforced: true
```

## Animation Mapping
- `generator.tripo.animations` lists desired output clip names.
- `generator.tripo.animation_presets` maps output clip names to Tripo preset names.
- The exporter preserves output clip names in the final GLB, while Tripo is called with mapped preset names.
- If either `generator.tripo.animations` or `generator.tripo.animation_presets` keys are provided, only those clip
  outputs are requested.
- Character defaults (`idle`, `walk`, `run`) are injected only when no animation outputs are specified.
- GLB export is Actions-only and excludes NLA strips, so exported files do not include `NlaTrack*` animations.
- If `generator.tripo.in_place` is `true`, normalize tries to zero root XY motion and reports
  `root_motion_zeroed`, `root_motion_max_xy`, and `root_motion_root_bone`.

## Animation Source Modes
- `generator.tripo.animation_source: tripo` (default) uses Tripo for all requested clips.
- `generator.tripo.animation_source: curated` requires local clip files for every requested clip.
- `generator.tripo.animation_source: curated_then_tripo` uses local clips first and calls Tripo only for missing clips.
- Optional local mapping:
  - `generator.tripo.curated_animation_dir`: folder containing `clips/` and optional `retarget_map.json`.
  - `generator.tripo.curated_clip_files`: output clip name -> relative file path under the curated folder.

## Curated Drop-In Contract
- Folder: `assets_pipeline/animation_library/<character_name>/`
- Clip files: `clips/<clip_name>.glb|fbx|gltf`
- Optional map: `retarget_map.json` with `bone_map`, optional `hand_r_bone`, `hand_l_bone`, and `t_pose_offsets_deg`.

## Character QC Enforcement
- Set `character_qc_enforced: true` on a character spec to enable auto-fix then fail-by-default gating.
- Fail checks include:
  - `bone_scale_keys_remaining > 0`
  - `max_vertex_influences_after > 4`
  - in-place drift (`root_motion_max_xy > 0.02`)
  - missing requested clips
  - hand lock error threshold (`hand_lock_error_cm_max`)
  - ADS clip bake coverage (`ads_clips_targeted` must be covered by `ads_clips_baked` when `ads_enabled=true`)
  - ADS geometric thresholds (`left_hand_grip_error_cm_max <= 3.0`, `ads_eye_to_sight_m_max <= 0.20`, `ads_eye_weapon_alignment_deg_max <= 15.0`)
  - floating disconnected shards after cleanup (`floating_components_after > 0`)
  - movement QC failures from clip metrics (`movement_qc_failures`)
- Override only when you explicitly want degraded output:
  - `asset_factory build ... --allow-degraded-character`

## ADS Attach Fields (`loadout.primary_weapon.attach`)
- `grip_right_offset_m`: optional weapon-local anchor for right hand diagnostics.
- `grip_left_offset_m`: optional weapon-local left foregrip lock anchor.
- `sight_offset_m`: optional weapon-local sight anchor used for ADS aim targeting.
- `ads_eye_offset_m`: optional world-space eye offset added to resolved head-bone position during ADS solve.
- `ads_enabled`: enable ADS bake pass for this weapon attach.
- `ads_clip_names`: explicit output clip names to bake in ADS mode.
  If empty and `ads_enabled=true`, rifle locomotion clips from requested clips are targeted.
- `ads_aim_distance_m`: retained for backward compatibility with existing attach helpers.
- `ads_head_bone`: optional explicit head bone name.
- `ads_spine_bones`: optional explicit ordered spine/neck chain.

## Movement QC Report Fields
- `clip_motion_metrics` per exported clip:
  - `movement_kind`
  - `root_local_xy_max_m`
  - `foot_span_avg_m`
  - `foot_loop_avg_m`
- `movement_qc_failures`: deterministic locomotion checks collected during normalize.
- `locomotion_clips_checked`: number of idle/walk/run/strafe clips evaluated.

## Character Model Source
- `generator.character_model_source: full_body` (default) keeps existing full-body generation.
- `generator.character_model_source: base_human_hybrid` enables optional base-human contract mode.
- Contract path for base human: `assets_pipeline/base_humans/<character_name>/base_human_rigged.glb`
