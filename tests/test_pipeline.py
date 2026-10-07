"""Pipeline execution tests with a scripted fake provider."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path

from localization.config import Settings
from localization.models import Batch, RunStats, TranslationUnit, UnitStatus
from localization.pipeline import Pipeline, _parse_review_response, _parse_translation_response
from localization.profile import load_profile, runtime_core

REPO_ROOT = Path(__file__).resolve().parent.parent


def make_unit(i: int, text: str = "A normal sentence to translate.") -> TranslationUnit:
    return TranslationUnit(
        unit_id=f"u{i}",
        source=text,
        source_hash=f"h{i}",
        path="file.json",
    )


def translations_json(batch: Batch, make) -> str:
    out = {u.unit_id: make(u) for u in batch.units}
    return json.dumps({"translations": out}, ensure_ascii=False)


def resp(content: str) -> object:
    return type("Resp", (), {"content": content, "finish_reason": "stop"})()


class ScriptedProvider:
    """Returns scripted responses; records prompts for assertions."""

    def __init__(self, script=None):
        self.script = list(script or [])
        self.calls: list[str] = []
        self.barrier: threading.Barrier | None = None

    def complete(self, messages, batch: Batch, attempt: int):
        self.calls.append(batch.batch_id)
        if self.barrier is not None:
            self.barrier.wait()
        if self.script:
            entry = self.script.pop(0)
            if callable(entry):
                return entry(messages, batch, attempt)
            if isinstance(entry, Exception):
                raise entry
            return entry
        default = translations_json(batch, lambda u: f"Tradução de {u.unit_id}")
        return resp(default)


class PipelineTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_cwd = Path.cwd()
        os.chdir(self.tmp.name)
        self.profile = load_profile(REPO_ROOT / "game-profile.json")

    def tearDown(self):
        pipe = getattr(self, "pipe", None)
        if pipe is not None:
            pipe.close()
        os.chdir(self.old_cwd)
        self.tmp.cleanup()

    def _pipe(self, script=None, **settings_extra) -> Pipeline:
        settings = Settings()
        settings.api_key = "test-key"
        for key, value in settings_extra.items():
            setattr(settings, key, value)
        self.pipe = Pipeline(settings, ScriptedProvider(script), self.profile, RunStats())
        return self.pipe


class ParseTests(unittest.TestCase):
    def test_translation_envelope_tolerant(self):
        batch = Batch(batch_id="b", units=[make_unit(0, "Hello")])
        valid, problems = _parse_translation_response(
            '{"translations": {"u0": "Olá"}}', batch
        )
        assert valid == {"u0": "Olá"}
        assert problems == {}
        # Missing id: reported, not fatal: the caller retries only that id.
        valid, problems = _parse_translation_response(
            '{"translations": {"u1": "Outro"}}', batch
        )
        assert valid == {}
        assert "u0" in problems
        valid, problems = _parse_translation_response(
            '{"translations": {"u0": ""}}', batch
        )
        assert valid == {}
        assert "empty or non-string" in problems["u0"]
        # Envelope garbage stays fatal.
        with self.assertRaises(ValueError):
            _parse_translation_response('{"other": 1}', batch)
        with self.assertRaises(ValueError):
            _parse_translation_response("not json at all", batch)

    def test_review_envelope_keep_exact(self):
        batch = Batch(batch_id="b", units=[make_unit(0, "Hello")])
        ok = _parse_review_response(
            '{"reviews": [{"id": "u0", "decision": "keep", "text": "Olá", "issue_codes": []}]}',
            batch,
            {"u0": "Olá"},
        )
        assert ok == {"u0": "Olá"}
        with self.assertRaises(ValueError):
            _parse_review_response(
                '{"reviews": [{"id": "u0", "decision": "keep", "text": "Mudado", "issue_codes": []}]}',
                batch,
                {"u0": "Olá"},
            )
        with self.assertRaises(ValueError):
            _parse_review_response(
                '{"reviews": [{"id": "u0", "decision": "revise", "text": "Olá", "issue_codes": []}]}',
                batch,
                {"u0": "Olá"},
            )


class PipelineTests(PipelineTestBase):
    def _run(self, pipe: Pipeline) -> tuple[dict, list]:
        from localization.batching import BatchPlanner

        profile_core = runtime_core(pipe.profile)
        units = [make_unit(i) for i in range(4)]
        batches = BatchPlanner(pipe.settings.batch_size).plan(units)
        return pipe.run_batches(batches, profile_core)

    def test_happy_path_accepts_all_and_checkpoints(self):
        pipe = self._pipe()
        accepted, failed = self._run(pipe)
        assert len(accepted) == 4
        assert failed == []
        rows = pipe.checkpoint.get_unit("u0")
        assert rows is not None
        assert rows["status"] == UnitStatus.ACCEPTED.value

    def test_partial_response_retries_missing_ids(self):
        def scripted(messages, batch, attempt):
            if attempt == 0:
                out = {u.unit_id: f"Texto {u.unit_id}" for u in batch.units if u.unit_id != "u1"}
                return resp(json.dumps({"translations": out}))
            return resp(
                json.dumps(
                    {"translations": {u.unit_id: f"Texto {u.unit_id}" for u in batch.units}}
                )
            )

        pipe = self._pipe([scripted])
        accepted, failed = self._run(pipe)
        assert len(accepted) == 4
        assert failed == []
        assert pipe.stats.retries >= 1

    def test_punctuation_violation_rejected_then_fixed(self):
        def scripted(messages, batch, attempt):
            if attempt == 0:
                return resp(
                    json.dumps(
                        {
                            "translations": {
                                u.unit_id: "Tradução — com travessão!" for u in batch.units
                            }
                        }
                    )
                )
            return resp(
                json.dumps(
                    {"translations": {u.unit_id: "Tradução sem travessão." for u in batch.units}}
                )
            )

        pipe = self._pipe([scripted])
        accepted, failed = self._run(pipe)
        assert len(accepted) == 4
        del failed
        assert all("—" not in v for v in accepted.values())
        assert pipe.stats.retries >= 1

    def test_provider_stop_error_aborts(self):
        from localization.provider import ProviderStopError

        pipe = self._pipe([ProviderStopError("auth failed")])
        with self.assertRaises(ProviderStopError):
            self._run(pipe)

    def test_concurrent_requests_overlap(self):
        barrier = threading.Barrier(2)
        pipe = self._pipe(
            provider_max_concurrency=2, translation_concurrency=2, batch_size=2
        )
        pipe.provider.barrier = barrier
        accepted, _failed = self._run(pipe)
        # The barrier only passes if both requests overlap in flight.
        assert len(accepted) == 4
        assert len(pipe.provider.calls) >= 2

    def test_glossary_seeded_from_profile(self):
        pipe = self._pipe()
        entries = pipe.glossary.relevant(["The Guardian class"], limit=10)
        assert any(e["term"] == "guardian" and e["status"] == "locked" for e in entries)
        assert pipe.glossary.outstanding() == 0


if __name__ == "__main__":
    unittest.main()