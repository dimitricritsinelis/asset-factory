#!/usr/bin/env python3
from __future__ import annotations

import base64
from contextlib import ExitStack
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from asset_builder_code.asset_factory.build import BuildOptions, build_asset
from asset_builder_code.asset_factory.cli import main
from asset_builder_code.asset_factory.config import Settings
from asset_builder_code.asset_factory.json_io import read_json_dict
from asset_builder_code.asset_factory.plan_schema import OpenAITripoPlan
from asset_builder_code.asset_factory.spec import canonical_smoke_spec_path, spec_sha256
from asset_builder_code.asset_factory.tripo_io import TripoResult


_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADElEQVR4nGNgYGBgAAAABAAB9hc4VQAAAABJRU5ErkJggg=="
)
_FIXTURE_DIR = Path(__file__).with_name("fixtures")


def _write_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_TINY_PNG)


def _write_enemy_raider_spec(path: Path, *, name: str = "enemy_raider", description: str = "Hostile raider NPC.") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                f"name: {name}",
                f"description: '{description}'",
                "style:",
                "  genre: realistic-competitive-fps",
                "  keywords:",
                "    - competitive shooter readability",
                "    - hooded mask",
                "scale_meters: 1.78",
                "tri_budget: 40000",
                "colors:",
                "  primary: '#C2A97A'",
                "  secondary: '#3F4A38'",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _settings(root: Path) -> Settings:
    return Settings(
        project_root=root,
        openai_api_key="test-openai",
        openai_text_model="gpt-test",
        openai_image_model="gpt-image-test",
        tripo_api_key="test-tripo",
        tripo_model_version="tripo-test",
        tripo_texture_quality="detailed",
        tripo_timeout_s=30,
    )


def _copy_repo_fixture(root: Path) -> None:
    dest = root / "tests" / "fixtures"
    dest.mkdir(parents=True, exist_ok=True)
    for src in _FIXTURE_DIR.iterdir():
        shutil.copyfile(src, dest / src.name)


def _new_call_counts() -> dict[str, int]:
    return {"plan": 0, "sheet": 0, "split": 0, "tripo": 0}


class OnlineBuildTest(unittest.TestCase):
    def _patch_online_pipeline(self, call_counts: dict[str, int]) -> ExitStack:
        plan = OpenAITripoPlan(
            version="1",
            asset_name="enemy_raider",
            reference_sheet_prompt="sheet prompt",
            tripo_prompt="tripo prompt",
            notes=["test"],
        )

        def fake_generate_plan(*, system_prompt: str, user_prompt: str, settings: Settings) -> OpenAITripoPlan:
            del system_prompt, user_prompt, settings
            call_counts["plan"] += 1
            return plan

        def fake_generate_reference_sheet(prompt: str, out_path: Path, settings: Settings) -> None:
            del settings
            call_counts["sheet"] += 1
            self.assertEqual(prompt, "sheet prompt")
            _write_png(out_path)

        def fake_split_reference_sheet(sheet: Path, front: Path, side: Path, back: Path) -> None:
            call_counts["split"] += 1
            self.assertTrue(sheet.exists())
            _write_png(front)
            _write_png(side)
            _write_png(back)

        def fake_generate_model_from_images(
            *,
            images: list[str],
            prompt: str,
            out_dir: Path,
            settings: Settings,
            face_limit: int,
            model_version: str,
            texture_quality: str,
        ) -> TripoResult:
            call_counts["tripo"] += 1
            self.assertEqual(len(images), 3)
            self.assertEqual(prompt, "tripo prompt")
            self.assertEqual(settings.tripo_api_key, "test-tripo")
            self.assertEqual(model_version, "tripo-test")
            self.assertEqual(texture_quality, "detailed")
            self.assertGreater(face_limit, 0)
            out_dir.mkdir(parents=True, exist_ok=True)
            model_path = out_dir / "model.glb"
            pbr_path = out_dir / "pbr.glb"
            rendered_path = out_dir / "rendered.png"
            task_path = out_dir / "task.json"
            model_path.write_bytes(b"model")
            pbr_path.write_bytes(b"pbr")
            rendered_path.write_bytes(_TINY_PNG)
            task_path.write_text('{"task_id":"task-123","status":"success"}', encoding="utf-8")
            return TripoResult(
                task_payload={"task_id": "task-123", "status": "success"},
                model_path=model_path,
                pbr_model_path=pbr_path,
                rendered_path=rendered_path,
            )

        stack = ExitStack()
        stack.enter_context(patch("asset_builder_code.asset_factory.build.generate_plan", side_effect=fake_generate_plan))
        stack.enter_context(
            patch(
                "asset_builder_code.asset_factory.build.generate_reference_sheet",
                side_effect=fake_generate_reference_sheet,
            )
        )
        stack.enter_context(
            patch(
                "asset_builder_code.asset_factory.build.split_reference_sheet",
                side_effect=fake_split_reference_sheet,
            )
        )
        stack.enter_context(
            patch(
                "asset_builder_code.asset_factory.build.generate_model_from_images",
                side_effect=fake_generate_model_from_images,
            )
        )
        return stack

    def test_build_requires_explicit_spec_path(self) -> None:
        with self.assertRaises(SystemExit) as exc:
            main(["build"])
        self.assertEqual(exc.exception.code, 2)

    def test_only_canonical_spec_path_is_supported(self) -> None:
        with tempfile.TemporaryDirectory(prefix="asset_factory_test.") as tmp:
            root = Path(tmp)
            other_spec = root / "asset_definitions" / "props" / "crate.yaml"
            _write_enemy_raider_spec(other_spec)

            with self.assertRaisesRegex(ValueError, "asset_definitions/enemy_raider.yaml"):
                build_asset("asset_definitions/props/crate.yaml", BuildOptions(force=True), _settings(root))

    def test_only_enemy_raider_name_is_supported(self) -> None:
        with tempfile.TemporaryDirectory(prefix="asset_factory_test.") as tmp:
            root = Path(tmp)
            smoke_spec = canonical_smoke_spec_path(root)
            _write_enemy_raider_spec(smoke_spec, name="crate")

            with self.assertRaisesRegex(ValueError, "enemy_raider"):
                build_asset("asset_definitions/enemy_raider.yaml", BuildOptions(force=True), _settings(root))

    def test_online_build_writes_fixed_production_outputs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="asset_factory_test.") as tmp:
            root = Path(tmp)
            smoke_spec = canonical_smoke_spec_path(root)
            _write_enemy_raider_spec(smoke_spec)

            settings = _settings(root)
            call_counts = _new_call_counts()

            with self._patch_online_pipeline(call_counts):
                paths = build_asset("asset_definitions/enemy_raider.yaml", BuildOptions(force=True), settings)

            self.assertEqual(paths.spec_path, smoke_spec)
            self.assertEqual(call_counts, {"plan": 1, "sheet": 1, "split": 1, "tripo": 1})
            self.assertEqual(
                str(paths.openai_plan_log.relative_to(root)),
                "generated_asset_files/openai_logs/enemy_raider/enemy_raider_openai_plan.json",
            )
            self.assertEqual(
                str(paths.ref_sheet.relative_to(root)),
                "generated_asset_files/openai_2d_reference_images/enemy_raider/enemy_raider_sheet.png",
            )
            self.assertEqual(
                str(paths.tripo_task_json.relative_to(root)),
                "generated_asset_files/tripo_3d_raw_outputs/enemy_raider/enemy_raider_task.json",
            )
            self.assertEqual(
                str(paths.glb_file.relative_to(root)),
                "generated_asset_files/final_3d_models/production/enemy_raider.glb",
            )
            self.assertEqual(
                str(paths.report_json.relative_to(root)),
                "generated_asset_files/build_reports/production/enemy_raider.json",
            )

            report = read_json_dict(paths.report_json)
            self.assertEqual(report["asset_name"], "enemy_raider")
            self.assertEqual(report["source_mode"], "online")
            self.assertEqual(report["spec_sha256"], spec_sha256(smoke_spec))
            self.assertEqual(
                report["openai_plan_path"],
                "generated_asset_files/openai_logs/enemy_raider/enemy_raider_openai_plan.json",
            )
            self.assertEqual(
                report["reference_sheet_path"],
                "generated_asset_files/openai_2d_reference_images/enemy_raider/enemy_raider_sheet.png",
            )
            self.assertEqual(
                report["reference_views"]["front"],
                "generated_asset_files/openai_2d_reference_images/enemy_raider/enemy_raider_front.png",
            )
            self.assertEqual(
                report["tripo_task_path"],
                "generated_asset_files/tripo_3d_raw_outputs/enemy_raider/enemy_raider_task.json",
            )
            self.assertEqual(report["export_path"], "generated_asset_files/final_3d_models/production/enemy_raider.glb")

    def test_repeated_online_build_overwrites_canonical_outputs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="asset_factory_test.") as tmp:
            root = Path(tmp)
            smoke_spec = canonical_smoke_spec_path(root)
            _write_enemy_raider_spec(smoke_spec)

            settings = _settings(root)
            call_counts = _new_call_counts()

            with self._patch_online_pipeline(call_counts):
                first = build_asset("asset_definitions/enemy_raider.yaml", BuildOptions(force=False), settings)
                second = build_asset("asset_definitions/enemy_raider.yaml", BuildOptions(force=False), settings)

            self.assertEqual(call_counts, {"plan": 2, "sheet": 2, "split": 2, "tripo": 2})
            self.assertEqual(first.glb_file, second.glb_file)
            self.assertEqual(
                str(second.glb_file.relative_to(root)),
                "generated_asset_files/final_3d_models/production/enemy_raider.glb",
            )

    def test_fixture_replay_uses_canonical_smoke_outputs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="asset_factory_test.") as tmp:
            root = Path(tmp)
            _copy_repo_fixture(root)

            smoke_spec = canonical_smoke_spec_path(root)
            _write_enemy_raider_spec(smoke_spec)

            settings = _settings(root)
            paths = build_asset(
                "asset_definitions/enemy_raider.yaml",
                BuildOptions(force=True, fixture_replay=True),
                settings,
            )
            self.assertEqual(
                str(paths.glb_file.relative_to(root)),
                "generated_asset_files/final_3d_models/tests/enemy_raider.glb",
            )
            self.assertEqual(
                str(paths.report_json.relative_to(root)),
                "generated_asset_files/build_reports/tests/enemy_raider.json",
            )

            report = read_json_dict(paths.report_json)
            self.assertEqual(report["asset_name"], "enemy_raider")
            self.assertEqual(report["source_mode"], "fixture_replay")
            self.assertEqual(
                report["reference_sheet_path"],
                "generated_asset_files/openai_2d_reference_images/enemy_raider/enemy_raider_sheet.png",
            )
            self.assertEqual(report["export_path"], "generated_asset_files/final_3d_models/tests/enemy_raider.glb")


if __name__ == "__main__":
    raise SystemExit(unittest.main())
