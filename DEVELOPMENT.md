# DEVELOPMENT.md

## Local Dev Loop
```bash
make setup
python -m asset_factory --help
make smoke
```

## Typical Iteration
1. Edit a spec in `assets_src/specs/`.
2. Run offline first:
   - `make build NAME=<name> ARGS="--skip-openai --skip-images --force"`
3. Run online if needed:
   - `make build NAME=<name> ARGS="--provider tripo --force"`
4. Inspect:
   - `assets/reports/<name>.json`
   - `assets/models/<name>.glb`
   - `assets_src/blender/<name>.blend`

## Debug Order
1. Check build log: `assets_src/logs/<name>.log`
2. Check report status/reasons: `assets/reports/<name>.json`
3. Re-run with `--force`.
4. Re-run with `--strict` when validating CI quality gates.

## Offline Reliability Contract
- `--skip-openai --skip-images` must still produce a valid GLB + report.
- If online providers fail, build should downgrade with `status: degraded` and reasons.

## Cleanup
```bash
make clean-temp
```
