# DEVELOPMENT.md

## Local Dev Loop
```bash
make setup
python -m asset_factory --help
make smoke
```

## Spec Directories
- `assets_pipeline/inputs/`: active specs
- `assets_pipeline/examples/`: examples for reference
- `assets_pipeline/tests/`: test specs

`build-all` scans only `assets_pipeline/inputs/`.

## Output Directories
- Production builds: `assets/{models,reports,thumbnails}/production/`
- Test builds: `assets/{models,reports,thumbnails}/tests/`

## Typical Iteration
1. Edit spec in `assets_pipeline/inputs/`.
2. Run offline first:
   - `make build NAME=<name> ARGS="--skip-openai --skip-images --force"`
3. Run online if needed:
   - `make build NAME=<name> ARGS="--provider tripo --force"`
4. Inspect:
   - `assets/reports/production/<name>.json`
   - `assets/models/production/<name>.glb`
   - `assets_pipeline/blender/<name>.blend`

## Debug Order
1. `assets_pipeline/logs/<name>.log`
2. `assets/reports/*/<name>.json`
3. Re-run with `--force`
4. Re-run with `--strict` for quality gate

## Cleanup
```bash
make clean-temp
```
