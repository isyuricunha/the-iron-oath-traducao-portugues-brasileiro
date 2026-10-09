"""Batching, checkpoint, config and CLI unit tests."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from localization import config
from localization.batching import BatchPlanner
from localization.checkpoint import Checkpoint
from localization.models import GenerationInfo, TranslationUnit


def make_unit(i: int, text: str = "Some translatable sentence here.") -> TranslationUnit:
    return TranslationUnit(
        unit_id=f"u{i}",
        source=text,
        source_hash=f"h{i}",
        path=f"file{i}.json",
    )


class BatchingTests(unittest.TestCase):
    def test_ceiling_never_exceeded(self):
        planner = BatchPlanner(batch_size=40, max_batch_chars=100_000)
        units = [make_unit(i) for i in range(100)]
        batches = planner.plan(units)
        assert len(batches) == 3
        assert all(len(b.units) <= 40 for b in batches)

    def test_char_cap_shrinks_batch(self):
        planner = BatchPlanner(batch_size=40, max_batch_chars=60)
        units = [make_unit(i, "word " * 30) for i in range(4)]
        batches = planner.plan(units)
        assert len(batches) > 1

    def test_split_invalid_halves(self):
        planner = BatchPlanner()
        units = [make_unit(i) for i in range(6)]
        parent = _parent_batch(units)
        children = planner.split_invalid(units, parent)
        assert len(children) == 2
        assert len(children[0].units) == 3
        assert len(children[1].units) == 3


def _parent_batch(units):
    from localization.models import Batch

    return Batch(batch_id="b0", units=units)


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.checkpoint = Checkpoint(self.base / "cp" / "checkpoint.db")

    def tearDown(self):
        self.checkpoint.close()
        self.tmp.cleanup()

    def _info(self, generation_id: str) -> GenerationInfo:
        return GenerationInfo(
            generation_id=generation_id,
            mode="translate",
            source_manifest="a.json",
            target_lang="ptbr",
            profile_fingerprint="fp1",
            policy_revision="pol-1",
            protection_revision="prot-1",
            base_generation=None,
            model_fingerprint="deepseek-v4-flash:max",
            staging=str(self.base / "staging"),
        )

    def test_generation_compat_and_resume(self):
        self.checkpoint.new_generation(self._info("g1"))
        accepted = self.checkpoint.accepted_units("g1")
        assert accepted == {}
        unit = make_unit(0)
        unit.accepted_target = "Texto"
        from localization.models import UnitStatus

        unit.status = UnitStatus.ACCEPTED
        self.checkpoint.save_units([unit], "g1")
        assert self.checkpoint.accepted_units("g1") == {"u0": "Texto"}
        latest = self.checkpoint.latest_compatible(self._info("gX"))
        assert latest is not None
        assert latest.generation_id == "g1"

    def test_incompatible_profile_not_reused(self):
        self.checkpoint.new_generation(self._info("g1"))
        info = self._info("g2")
        info.profile_fingerprint = "fp2"
        assert self.checkpoint.latest_compatible(info) is None

    def test_accepted_units_merge_across_generations(self):
        from localization.models import UnitStatus

        info_older = self._info("g1")
        info_older.created_at = "2026-10-07T01:00:00"
        self.checkpoint.new_generation(info_older)
        info_newer = self._info("g2")
        info_newer.created_at = "2026-10-07T02:00:00"
        self.checkpoint.new_generation(info_newer)

        def accept(gen: str, unit_id: int, target: str):
            unit = make_unit(unit_id)
            unit.status = UnitStatus.ACCEPTED
            unit.accepted_target = target
            self.checkpoint.save_units([unit], gen)

        accept("g1", 0, "Velho")
        accept("g1", 1, "So no g1")
        accept("g2", 0, "Novo")

        ids = self.checkpoint.compatible_generation_ids(self._info("gX"))
        assert ids == ["g2", "g1"]
        merged = self.checkpoint.accepted_units_merged(ids)
        assert merged == {"u0": "Novo", "u1": "So no g1"}


class ConfigTests(unittest.TestCase):
    def test_dotenv_forms(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                'PLAIN=one\nQUOTED="two words"\nSINGLE=\'three\'\nEXPORTED=export value\n'
                "# comment\nBADLINE without equals\n",
                encoding="utf-8",
            )
            values = config.load_dotenv(path)
            assert values["PLAIN"] == "one"
            assert values["QUOTED"] == "two words"
            assert values["SINGLE"] == "three"
            assert values["EXPORTED"] == "export value"

    def test_key_process_env_wins(self):
        old = os.environ.pop("FREEINFERENCE_API_KEY", None)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / ".env"
                path.write_text("FREEINFERENCE_API_KEY=from-file\n", encoding="utf-8")
                values = config.load_dotenv(path)
                assert config.resolve_key(values) == "from-file"
                os.environ["FREEINFERENCE_API_KEY"] = "from-process"
                assert config.resolve_key(values) == "from-process"
        finally:
            os.environ.pop("FREEINFERENCE_API_KEY", None)
            if old is not None:
                os.environ["FREEINFERENCE_API_KEY"] = old

    def test_startup_rejects_foreign_host_and_model(self):
        env = {"TRANSLATION_BASE_URL": "https://other.example/v1"}
        with self.assertRaises(ValueError):
            config.settings_from_env(env)
        env = {"TRANSLATION_MODEL": "gpt-9"}
        with self.assertRaises(ValueError):
            config.settings_from_env(env)
        env = {"TRANSLATION_REASONING_EFFORT": "medium"}
        with self.assertRaises(ValueError):
            config.settings_from_env(env)

    def test_env_example_exact_content(self):
        root = Path(__file__).resolve().parent.parent
        content = (root / ".env.example").read_text(encoding="utf-8")
        expected = (
            "# FreeInference API key (required for a real run). Create one at https://freeinference.org\n"
            "FREEINFERENCE_API_KEY=\n"
            "\n"
            "# Optional overrides. Leave them commented out to use the defaults shown.\n"
            "# TRANSLATION_BASE_URL=https://freeinference.org/v1\n"
            "# TRANSLATION_MODEL=deepseek-v4-flash\n"
            "# GLOSSARY_MODEL=\n"
            "# TRANSLATION_REASONING_EFFORT=max\n"
            "# TRANSLATION_STRIP_DIACRITICS=1\n"
        )
        assert content == expected

    def test_input_output_same_path_rejected(self):
        settings = config.Settings()
        settings.input_root = Path("/tmp/x").resolve()
        settings.output_root = Path("/tmp/x").resolve()
        with self.assertRaises(ValueError):
            settings.validate_profile()


if __name__ == "__main__":
    unittest.main()