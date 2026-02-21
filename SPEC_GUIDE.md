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
  provider: tripo
  tripo:
    mode: multiview
    rig: true
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
    muzzle_socket_name: muzzle
```

## Animation Mapping
- `generator.tripo.animations` lists desired output clip names.
- `generator.tripo.animation_presets` maps output clip names to Tripo preset names.
- The exporter preserves output clip names in the final GLB, while Tripo is called with mapped preset names.
