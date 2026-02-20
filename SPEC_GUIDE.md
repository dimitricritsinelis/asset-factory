# SPEC_GUIDE.md

## Minimal Spec
```yaml
name: stylized_crate
kind: prop
description: Stylized wooden crate for FPS map dressing.
style:
  genre: stylized
  keywords: [crate, wood, game-ready]
scale_meters: 1.0
tri_budget: 3000
texture:
  generate_basecolor: false
  basecolor_size: 512
colors:
  primary: "#8C6B45"
  secondary: "#4E3A28"
seed: 1337
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

## Notes
- `kind: character` defaults to Tripo provider unless overridden.
- `kind: prop/environment` defaults to procedural unless overridden.
- For weapons, set `runtime_output_path` or rely on default:
  - `apps/client/public/assets/models/weapons/<name>/<name>.glb`
