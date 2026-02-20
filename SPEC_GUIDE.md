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
