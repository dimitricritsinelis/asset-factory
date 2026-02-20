from __future__ import annotations

import argparse


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="asset_factory",
        description="OpenAI + Tripo3D asset factory with Blender normalization",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    build_cmd = subparsers.add_parser("build", help="Build one asset from YAML spec")
    build_cmd.add_argument("spec_path_or_name", help="YAML path or bare spec name")
    build_cmd.add_argument("--force", action="store_true", help="Regenerate all artifacts")
    build_cmd.add_argument(
        "--skip-images",
        action="store_true",
        help="Skip Images API step (continue without ref image if missing)",
    )
    build_cmd.add_argument(
        "--skip-openai",
        action="store_true",
        help="Offline mode: skip OpenAI and generate placeholder recipe",
    )
    build_cmd.add_argument(
        "--provider",
        choices=["procedural", "tripo"],
        default=None,
        help="Override generator provider for this build",
    )
    rig_group = build_cmd.add_mutually_exclusive_group()
    rig_group.add_argument("--rig", dest="rig_override", action="store_true", help="Force Tripo rig step")
    rig_group.add_argument("--no-rig", dest="rig_override", action="store_false", help="Disable Tripo rig step")
    build_cmd.set_defaults(rig_override=None)
    build_cmd.add_argument("--dry-run", action="store_true", help="Plan only; no API calls or Blender")
    build_cmd.add_argument(
        "--strict",
        action="store_true",
        help="Fail with non-zero exit if build completes in degraded mode",
    )
    build_cmd.set_defaults(handler=_handle_build)

    build_all_cmd = subparsers.add_parser("build-all", help="Build all specs in assets_src/specs")
    build_all_cmd.add_argument("--force", action="store_true", help="Regenerate all artifacts")
    build_all_cmd.add_argument("--skip-images", action="store_true", help="Skip Images API step")
    build_all_cmd.add_argument(
        "--skip-openai",
        action="store_true",
        help="Offline mode: skip OpenAI and generate placeholder recipes",
    )
    build_all_cmd.add_argument(
        "--provider",
        choices=["procedural", "tripo"],
        default=None,
        help="Override generator provider for all specs in this run",
    )
    rig_all_group = build_all_cmd.add_mutually_exclusive_group()
    rig_all_group.add_argument("--rig", dest="rig_override", action="store_true", help="Force Tripo rig step")
    rig_all_group.add_argument("--no-rig", dest="rig_override", action="store_false", help="Disable Tripo rig step")
    build_all_cmd.set_defaults(rig_override=None)
    build_all_cmd.add_argument("--dry-run", action="store_true", help="Plan only; no API calls or Blender")
    build_all_cmd.add_argument(
        "--strict",
        action="store_true",
        help="Fail with non-zero exit if any build completes in degraded mode",
    )
    build_all_cmd.set_defaults(handler=_handle_build_all)

    new_cmd = subparsers.add_parser("new", help="Create a new YAML spec from natural language")
    new_cmd.add_argument("name", help="Spec name (file will be assets_src/specs/<name>.yaml)")
    new_cmd.add_argument("--desc", required=True, help="Short description of the asset")
    new_cmd.set_defaults(handler=_handle_new)

    return parser


def _handle_build(args: argparse.Namespace) -> int:
    from .build import BuildOptions, build_asset, iter_artifact_paths
    from .config import load_settings

    settings = load_settings()
    options = BuildOptions(
        force=args.force,
        skip_images=args.skip_images,
        skip_openai=args.skip_openai,
        provider_override=args.provider,
        rig_override=args.rig_override,
        dry_run=args.dry_run,
        strict=args.strict,
    )
    paths = build_asset(args.spec_path_or_name, options, settings)

    print("Build complete:")
    for path in iter_artifact_paths(paths):
        print(f"- {path}")
    return 0


def _handle_build_all(args: argparse.Namespace) -> int:
    from .build import BuildOptions, build_all
    from .config import load_settings

    settings = load_settings()
    options = BuildOptions(
        force=args.force,
        skip_images=args.skip_images,
        skip_openai=args.skip_openai,
        provider_override=args.provider,
        rig_override=args.rig_override,
        dry_run=args.dry_run,
        strict=args.strict,
    )
    build_all(options, settings)
    print("Build-all complete")
    return 0


def _handle_new(args: argparse.Namespace) -> int:
    from .build import create_new_spec
    from .config import load_settings

    settings = load_settings()
    out_path = create_new_spec(args.name, args.desc, settings)
    print(f"Created spec: {out_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.handler(args)
