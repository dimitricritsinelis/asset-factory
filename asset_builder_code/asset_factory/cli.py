from __future__ import annotations

import argparse
import logging


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="asset_factory",
        description="Fixed enemy_raider OpenAI -> Tripo asset factory",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_cmd = subparsers.add_parser("build", help="Build the fixed enemy_raider asset")
    build_cmd.add_argument(
        "spec_path",
        help="Asset definition path relative to the repo root. Only asset_definitions/enemy_raider.yaml is supported.",
    )
    build_cmd.add_argument("--force", action="store_true", help="Overwrite generated build artifacts")
    build_cmd.add_argument("--fixture-replay", action="store_true", help="Replay committed offline fixture files")
    build_cmd.set_defaults(handler=_handle_build)
    return parser


def _handle_build(args: argparse.Namespace) -> int:
    from .build import BuildOptions, build_asset, iter_artifact_paths
    from .config import load_settings

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = load_settings()
    options = BuildOptions(force=args.force, fixture_replay=args.fixture_replay)
    paths = build_asset(args.spec_path, options, settings)
    print("Build complete:")
    for path in iter_artifact_paths(paths):
        print(f"- {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.handler(args)
