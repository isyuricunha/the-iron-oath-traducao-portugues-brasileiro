"""Pipeline orchestration: waves, validation, checkpoints, publication.

The execution model follows the spec:

* bounded waves with real concurrent provider requests (default 3);
* results applied on the main thread in deterministic batch order;
* accepted batches checkpointed incrementally: a batch commits as soon
  as every earlier batch in the current deterministic order is ready;
* glossary tasks may use one slot only while at least two main
  translation slots stay free; total in-flight never exceeds the
  provider limit;
* publication builds complete outputs in staging, validates them, then
  promotes atomically, preserving the previous valid output.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import formats
from .batching import BatchPlanner
from .checkpoint import Checkpoint
from .glossary import Glossary
from .models import Batch, GenerationInfo, RunStats, TranslationUnit, UnitStatus
from .prompts import build_review_prompt, build_translation_prompt, retry_feedback_for
from .protection import (
    ProtectionError,
    protect,
    restore,
    validate_punctuation,
    validate_tokens,
)
from .provider import ProviderStopError, TransientProviderError

PROFILE_PATH = Path("game-profile.json")


class ResponseError(ValueError):
    pass


def _guard_rebuild_validity(input_root: Path, relpath: str, rebuilt: bytes) -> None:
    """Refuse to stage a rebuilt HJSON file that lost strict-parse validity.

    The toolkit's own lenient parser accepts the game's HJSON dialect, so
    validity is compared with the strict reference parser and only when
    the original file itself parses cleanly. A missing hjson module
    disables the guard instead of blocking publication.
    """
    try:
        import hjson
    except ImportError:
        return
    original = (input_root / relpath).read_bytes().decode("utf-8-sig")
    try:
        hjson.loads(original)
    except Exception:  # noqa: BLE001 - original dialect quirks are not our failure
        return
    rebuilt_text = rebuilt.decode("utf-8-sig")
    try:
        hjson.loads(rebuilt_text)
    except Exception as exc:  # noqa: BLE001 - any parse failure must block staging
        raise ValueError(f"{relpath}: rebuild produced invalid HJSON: {exc}") from None


def _parse_review_response(raw: str, batch: Batch, currents: dict[str, str]) -> dict[str, str]:
    """Parse and envelope-validate a review response strictly."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ResponseError(f"response is not valid JSON: {exc}") from exc
    reviews = data.get("reviews") if isinstance(data, dict) else None
    if not isinstance(reviews, list):
        raise ResponseError("response lacks the reviews array")
    ids = [r.get("id") for r in reviews]
    if set(ids) != batch.unit_ids:
        missing = batch.unit_ids - set(ids)
        extra = set(ids) - batch.unit_ids
        problems = []
        if missing:
            problems.append(f"missing ids: {sorted(missing)[:5]}")
        if extra:
            problems.append(f"extra ids: {sorted(extra)[:5]}")
        raise ResponseError("; ".join(problems))
    out: dict[str, str] = {}
    for review in reviews:
        unit_id = review.get("id")
        decision = review.get("decision")
        text = review.get("text")
        if decision not in ("keep", "revise"):
            raise ResponseError(f"id {unit_id!r} has invalid decision {decision!r}")
        if not isinstance(text, str) or not text.strip():
            raise ResponseError(f"id {unit_id!r} has an empty or non-string text")
        if decision == "keep":
            current = currents.get(unit_id)
            if text != current:
                raise ResponseError(
                    f"id {unit_id!r}: keep decision changed the supplied translation"
                )
        else:
            if text == currents.get(unit_id):
                raise ResponseError(
                    f"id {unit_id!r}: revise decision returned the unchanged text"
                )
        out[unit_id] = text
    return out


def _parse_translation_response(
    raw: str, batch: Batch
) -> tuple[dict[str, str], dict[str, str]]:
    """Parse a translation response tolerantly.

    Returns (valid, problems): `valid` holds every requested id with a
    non-empty string value; `problems` maps each missing or malformed
    requested id to a reason. The caller accepts `valid` immediately and
    retries only the ids in `problems`. Envelope-level garbage (unparseable
    JSON, missing translations object) still raises ResponseError.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ResponseError(f"response is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or "translations" not in data:
        raise ResponseError("response lacks the translations object")
    translations = data["translations"]
    if not isinstance(translations, dict):
        raise ResponseError("translations is not an object")
    valid: dict[str, str] = {}
    problems: dict[str, str] = {}
    for unit_id in batch.unit_ids:
        value = translations.get(unit_id)
        if isinstance(value, str) and value.strip():
            valid[unit_id] = value
        elif value is None:
            problems[unit_id] = "missing id in the response"
        else:
            problems[unit_id] = "empty or non-string value"
    return valid, problems


_GLOSSARY_SEED_KEYS = {"locked", "confirmed"}


def seed_glossary(glossary: Glossary, profile: dict, generation: str | None = None) -> None:
    for entity in profile["entities"]:
        target = entity.get("target")
        if not target:
            continue
        glossary.upsert(
            term=entity["name"],
            sense=entity.get("type", ""),
            status="locked",
            target=target,
            confidence=0.95,
            evidence=entity.get("note") and [entity["note"]] or [],
            generation=generation,
        )


class ConcurrencyController:
    """One global in-flight budget with main-translation priority."""

    def __init__(self, provider_max: int, min_main_slots: int):
        self.provider_max = provider_max
        self.min_main_slots = min_main_slots
        self.active_main = 0
        self.active_worker = 0
        self.lock = threading.Lock()

    def acquire_main(self) -> bool:
        with self.lock:
            if self.active_main + self.active_worker >= self.provider_max:
                return False
            self.active_main += 1
            return True

    def release_main(self) -> None:
        with self.lock:
            self.active_main -= 1

    def acquire_worker(self) -> bool:
        with self.lock:
            if self.active_worker >= 1:
                return False
            if self.active_main + self.active_worker >= self.provider_max:
                return False
            if self.active_main > self.provider_max - 2:
                return False
            self.active_worker += 1
            return True

    def release_worker(self) -> None:
        with self.lock:
            self.active_worker -= 1


@dataclass
class BatchOutcome:
    batch: Batch
    accepted: dict[str, TranslationUnit]
    failed: list[TranslationUnit]
    attempts: int


def _problems_text(batch: Batch) -> str:
    """Bounded corrective feedback built from the batch failure state."""
    if not batch.failure:
        return "Every requested id must appear exactly once with a non-empty value."
    return batch.failure


class Pipeline:
    def __init__(
        self,
        settings,
        provider,
        profile: dict,
        stats: RunStats | None = None,
    ):
        self.settings = settings
        self.provider = provider
        self.profile = profile
        self.stats = stats or RunStats()
        self.generation_id = time.strftime("%Y%m%d%H%M%S")
        self.checkpoint_dir = Path(".checkpoints")
        self.checkpoint = Checkpoint(self.checkpoint_dir / "checkpoint.db")
        self.glossary = Glossary(self.checkpoint_dir / "glossary.db")
        self.controller = ConcurrencyController(
            settings.provider_max_concurrency, settings.min_translation_slots
        )
        seed_glossary(self.glossary, self.profile, self.generation_id)

    def close(self) -> None:
        self.checkpoint.close()
        self.glossary.close()

    # ---------------- extraction ----------------

    def extract_all(self, input_root: Path) -> dict[str, list[TranslationUnit]]:
        discovered = formats.discover(input_root, self.settings.format)
        by_format: dict[str, list[TranslationUnit]] = {}
        for handler_name, paths in discovered.items():
            handler = formats.HANDLERS[handler_name]
            units: list[TranslationUnit] = []
            for relpath in paths:
                extracted, _ = handler.extract(input_root, relpath)
                units.extend(extracted)
            by_format[handler_name] = units
        return by_format

    # ---------------- generation ----------------

    def open_generation(
        self, mode: str, source_manifest: str, resume: bool, staging: Path
    ) -> tuple[str, dict[str, str]]:
        info = GenerationInfo(
            generation_id=self.generation_id,
            mode=mode,
            source_manifest=source_manifest,
            target_lang=self.settings.target_lang,
            profile_fingerprint=str(self.profile.get("revision")),
            policy_revision="pol-1",
            protection_revision="prot-1",
            base_generation=None,
            model_fingerprint=f"{self.settings.model}:{self.settings.reasoning_effort}",
            staging=str(staging),
            created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )
        reused: dict[str, str] = {}
        if resume:
            compatible = self.checkpoint.compatible_generation_ids(info)
            if compatible:
                info.base_generation = compatible[0]
                reused = self.checkpoint.accepted_units_merged(compatible)
        self.checkpoint.new_generation(info)
        return self.generation_id, reused

    # ---------------- batch execution ----------------

    def run_batches(self, batches: list[Batch], profile_core: str) -> tuple[dict[str, str], list[TranslationUnit]]:
        """Execute batches with real concurrency and deterministic commit."""
        accepted: dict[str, str] = {}
        failed: list[TranslationUnit] = []
        results: dict[str, BatchOutcome | ProviderStopError] = {}
        committed_prefix = 0
        done_events: dict[str, threading.Event] = {b.batch_id: threading.Event() for b in batches}
        lock = threading.Lock()
        next_batch_idx = 0

        def runner() -> None:
            nonlocal next_batch_idx
            while True:
                with lock:
                    if next_batch_idx >= len(batches):
                        return
                    batch = batches[next_batch_idx]
                    next_batch_idx += 1
                try:
                    outcome = self._translate_with_recovery(batch, profile_core)
                except ProviderStopError as exc:
                    outcome = exc
                with lock:
                    results[batch.batch_id] = outcome
                done_events[batch.batch_id].set()

        threads = [
            threading.Thread(target=runner, daemon=True)
            for _ in range(min(self.settings.provider_max_concurrency, len(batches)))
        ]
        for t in threads:
            t.start()

        # Deterministic commit WHILE the threads run: a batch commits as
        # soon as every earlier batch is ready, so progress and checkpoints
        # stream out during the run instead of piling up until the end.
        start_time = time.monotonic()
        total_units = sum(len(b.units) for b in batches)
        while committed_prefix < len(batches):
            batch = batches[committed_prefix]
            done_events[batch.batch_id].wait()
            outcome = results[batch.batch_id]
            if isinstance(outcome, ProviderStopError):
                raise outcome from None
            saves: list[TranslationUnit] = []
            for unit in outcome.accepted.values():
                if unit.accepted_target is not None:
                    accepted[unit.unit_id] = unit.accepted_target
                    saves.append(unit)
            for unit in outcome.failed:
                if unit.unit_id not in accepted:
                    saves.append(unit)
            failed.extend(
                unit for unit in outcome.failed if unit.unit_id not in accepted
            )
            self.checkpoint.save_units(saves, self.generation_id)
            committed_prefix += 1
            self.stats.waves = (self.stats.waves or 0) + 1
            if committed_prefix == 1 or committed_prefix % 5 == 0 or committed_prefix == len(batches):
                elapsed = time.monotonic() - start_time
                rate = committed_prefix / elapsed if elapsed > 0 else 0
                remaining = len(batches) - committed_prefix
                eta = remaining / rate if rate > 0 else 0
                accepted_now = len(accepted)
                print(
                    f"[progress] batches {committed_prefix}/{len(batches)} "
                    f"committed; units accepted {accepted_now}/{total_units}; "
                    f"failed {len(failed)}; elapsed {elapsed/60:.1f}m; "
                    f"ETA {eta/60:.1f}m",
                    flush=True,
                )
        for t in threads:
            t.join()
        return accepted, failed

    def _translate_with_recovery(self, batch: Batch, profile_core: str) -> BatchOutcome:
        """Retry, accept valid subsets immediately, split persistent failures.

        Every successful request accepts its valid subset right away; only
        the still-missing or invalid units are retried (as a child batch
        with corrective feedback), so one defective unit never blocks or
        re-sends the units that already passed.
        """
        accepted_units: dict[str, TranslationUnit] = {}
        failed_units: list[TranslationUnit] = []
        open_batches: list[Batch] = [batch]
        budget = self.settings.retries + 4
        while open_batches and budget > 0:
            current = open_batches.pop(0)
            for attempt in range(self.settings.retries + 1):
                if budget <= 0:
                    break
                budget -= 1
                try:
                    outcome = self._translate_batch(current, profile_core, attempt)
                    for unit_id, target in outcome.items():
                        unit = next(u for u in current.units if u.unit_id == unit_id)
                        unit.accepted_target = target
                        unit.status = UnitStatus.ACCEPTED
                        accepted_units[unit_id] = unit
                    self.stats.batches += 1
                    remaining = [u for u in current.units if u.unit_id not in accepted_units]
                    if remaining and budget > 0:
                        self.stats.retries += 1
                        self._count_retry("partial_recovery")
                        child = Batch(
                            batch_id=f"{current.batch_id}r",
                            units=remaining,
                            reason="partial",
                            failure=_problems_text(current),
                        )
                        open_batches.insert(0, child)
                    break
                except ResponseError as exc:
                    current.failure = str(exc)
                    self.stats.retries += 1
                    self._count_retry("validation")
                except TransientProviderError as exc:
                    current.failure = str(exc)
                    self.stats.retries += 1
                    self._count_retry(type(exc).__name__)
                except ProviderStopError:
                    raise
                except Exception as exc:  # noqa: BLE001 - unexpected errors retry once
                    current.failure = str(exc)
                    self.stats.retries += 1
                    self._count_retry(type(exc).__name__)
            else:
                remaining = [u for u in current.units if u.unit_id not in accepted_units]
                if len(remaining) > 1 and budget > 0:
                    planner = BatchPlanner(self.settings.batch_size, self.settings.max_batch_chars)
                    open_batches = planner.split_invalid(remaining, current) + open_batches
                else:
                    failed_units.extend(remaining)
        for leftover in open_batches:
            failed_units.extend(u for u in leftover.units if u.unit_id not in accepted_units)
        for unit in batch.units:
            if unit.unit_id not in accepted_units and unit not in failed_units:
                failed_units.append(unit)
        return BatchOutcome(batch=batch, accepted=accepted_units, failed=failed_units, attempts=0)

    def _count_retry(self, reason: str) -> None:
        self.stats.retries_by_reason.setdefault(reason, 0)
        self.stats.retries_by_reason[reason] += 1

    def _translate_batch(
        self, batch: Batch, profile_core: str, attempt: int
    ) -> dict[str, str]:
        """One provider round trip for a batch; tolerant per-unit validation.

        Returns the accepted subset: items that fail token, punctuation or
        restore validation are recorded on the batch failure state with the
        exact reason and retried by the recovery loop, never blocking the
        units that passed.
        """
        requests = []
        review_current: dict[str, str] = {}
        for unit in batch.units:
            protected = protect(unit.source)
            unit.protected_source = protected.protected
            unit.tokens = protected.tokens
            unit_ctx = dict(unit.context)
            unit_ctx["token_context"] = [
                {"token": t.token, "category": t.category.value, "raw_value": t.raw}
                for t in protected.tokens
            ]
            if self.settings.mode == "review":
                current = unit.accepted_target or ""
                review_current[unit.unit_id] = current
                unit_ctx["current"] = current
            requests.append({"id": unit.unit_id, "source": unit.protected_source, "context": unit_ctx})
        if self.settings.mode == "review":
            prompt = build_review_prompt(
                profile_core,
                self.glossary if self.settings.glossary_enabled else None,
                requests,
                retry_feedback=retry_feedback_for(batch.failure) if batch.failure else None,
                project_context=self.settings.project_context,
            )
        else:
            prompt = build_translation_prompt(
                profile_core,
                self.glossary if self.settings.glossary_enabled else None,
                requests,
                retry_feedback=retry_feedback_for(batch.failure) if batch.failure else None,
                project_context=self.settings.project_context,
            )
        response = self.provider.complete(
            [
                {"role": "system", "content": "You translate game text to Brazilian Portuguese."},
                {"role": "user", "content": prompt},
            ],
            batch,
            attempt,
        )
        if self.settings.mode == "review":
            valid = _parse_review_response(response.content, batch, review_current)
            problems: dict[str, str] = {}
        else:
            valid, problems = _parse_translation_response(response.content, batch)
        validated: dict[str, str] = {}
        for unit_id, raw_target in valid.items():
            unit = next(u for u in batch.units if u.unit_id == unit_id)
            by_token = {t.token: t for t in unit.tokens}
            try:
                validate_tokens(unit.tokens, raw_target, by_token)
                violations = validate_punctuation(raw_target)
                if violations:
                    raise ProtectionError("; ".join(violations))
                final = restore(raw_target, by_token)
            except ProtectionError as exc:
                problems[unit_id] = str(exc)
                continue
            validated[unit_id] = final
        if problems:
            reasons = sorted(set(problems.values()))
            batch.failure = "rejected units: " + "; ".join(reasons[:3])
        return validated

    # ---------------- publication ----------------

    def build_staging(
        self,
        input_root: Path,
        accepted: dict[str, str],
        staging: Path,
        interface_map: dict | None = None,
    ) -> dict:
        """Build the complete player overlay in staging.

        `accepted` maps unit ids to targets for the provider-driven
        formats (hjson, yarn, textasset_xml) and the I2 table. The
        optional `interface_map` carries the hand-written sections
        ({scenes: {en: pt}, dll: {type: {en: pt}}}) patched surgically.
        Returns a report with per-source patch counts.
        """
        import shutil as _shutil

        from .formats.dll_format import DllFormat
        from .formats.i2_mono_format import I2MonoFormat
        from .formats.scene_mono_format import SceneMonoFormat

        interface_map = interface_map or {}
        scene_map: dict[str, str] = interface_map.get("scenes", {})
        dll_map: dict[str, dict[str, str]] = interface_map.get("dll", {})
        report: dict = {}

        discovered = formats.discover(input_root, self.settings.format)
        _shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        for handler_name, paths in discovered.items():
            handler = formats.HANDLERS[handler_name]
            if handler_name == "hjson":
                for relpath in paths:
                    rebuilt = handler.rebuild(input_root, relpath, accepted)
                    _guard_rebuild_validity(input_root, relpath, rebuilt)
                    target = staging / relpath
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(rebuilt)
            elif handler_name == "yarn":
                command_total = 0
                for relpath in paths:
                    rebuilt = handler.rebuild(
                        input_root, relpath, accepted, (interface_map or {}).get("yarn_commands")
                    )
                    if interface_map and interface_map.get("yarn_commands"):
                        problems = handler.validate_rebuilt(rebuilt, interface_map["yarn_commands"])
                        if problems:
                            raise ValueError(f"yarn command validation failed: {problems[:3]}")
                    _, command_report = handler.apply_command_map(
                        (input_root / relpath).read_bytes().decode("utf-8-sig"),
                        interface_map["yarn_commands"],
                    )
                    command_total += command_report["patched"]
                    target = staging / relpath
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(rebuilt)
                report["yarn_commands"] = {"patched": command_total}
            elif handler_name == "textasset_xml":
                relpath = paths[0]
                xml = handler.rebuild(input_root, relpath, accepted)
                target = staging / relpath
                target.parent.mkdir(parents=True, exist_ok=True)
                handler.write_asset(input_root, target, xml)
                # Validate the written asset by reloading it.
                import UnityPy

                env = UnityPy.load(str(target))
                found = False
                for obj in env.objects:
                    if obj.type.name == "TextAsset":
                        data = obj.read_typetree()
                        if data.get("m_Name") == "LocalizationText":
                            found = True
                            xml_back = str(data.get("m_Script") or "")
                            expected = {uid: v for uid, v in accepted.items() if uid.startswith("textasset:")}
                            from .formats.textasset_xml_format import TextAssetXmlFormat

                            mismatches = TextAssetXmlFormat.validate_str(xml_back, expected)
                            if mismatches:
                                raise ValueError(
                                    f"textasset validation found {mismatches} mismatches"
                                )
                if not found:
                    raise ValueError("resources.assets rebuilt without LocalizationText")
            elif handler_name == "i2_mono":
                translations = {
                    uid.split(":", 1)[1]: value
                    for uid, value in accepted.items()
                    if uid.startswith("i2:")
                }
                target = staging / I2MonoFormat.ASSET_PATH
                target.parent.mkdir(parents=True, exist_ok=True)
                # The staging copy already carries the LocalizationText
                # patch from the textasset_xml branch; patch the table
                # into that same file.
                write_report = I2MonoFormat.write_asset(target, translations)
                problems = I2MonoFormat.validate_table(target, translations)
                if problems:
                    raise ValueError(f"I2 table validation failed: {problems[:3]}")
                diff = _asset_object_diff(
                    input_root / I2MonoFormat.ASSET_PATH,
                    target,
                    allowed_textassets={"LocalizationText"},
                    allowed_monobehaviours={write_report["path_id"]},
                )
                if diff:
                    raise ValueError(f"resources.assets diff includes unintended objects: {diff[:3]}")
                report["i2_mono"] = {**write_report, "problems": 0}
            elif handler_name == "scene_mono":
                hits: dict[str, int] = {}
                for relpath in paths:
                    src = input_root / relpath
                    dst = staging / relpath
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    _shutil.copy2(src, dst)
                    file_report = SceneMonoFormat.patch_file(dst, scene_map)
                    for source, count in file_report["hits"].items():
                        hits[source] = hits.get(source, 0) + count
                    problems = SceneMonoFormat.validate_file(
                        dst, {k: scene_map[k] for k in file_report["hits"]}
                    )
                    if problems:
                        raise ValueError(f"scene validation failed for {relpath}: {problems[:3]}")
                    diff = _asset_object_diff(
                        input_root / relpath,
                        dst,
                        allowed_monobehaviours=set(file_report["touched"]),
                    )
                    if diff:
                        raise ValueError(f"{relpath} diff includes unintended objects: {diff[:3]}")
                if scene_map and set(hits) != set(scene_map):
                    missing = sorted(set(scene_map) - set(hits))
                    raise ValueError(f"scene map entries never found at verified sites: {missing[:5]}")
                report["scene_mono"] = {"files": len(paths), "patched_strings": len(hits), "hits": hits}
            elif handler_name == "dll" and dll_map:
                target = staging / DllFormat.MANAGED_DIRECTORY / DllFormat.ASSEMBLY_NAME
                patch_report = DllFormat.patch(input_root, target, dll_map)
                problems = DllFormat.validate_patched(target, dll_map)
                if problems:
                    raise ValueError(f"dll validation failed: {problems[:3]}")
                report["dll"] = {**patch_report}
        # Copy the untouched mirror tree so PTBR stays copy-ready. The
        # repository's own artifacts (git dir, env files, toolkit) must
        # never ship inside the player overlay.
        skip_names = {
            ".git",
            ".gitignore",
            ".gitattributes",
            ".env",
            ".env.example",
            "__pycache__",
            ".pytest_cache",
            ".ruff_cache",
            "tools",
            "localization",
            "tests",
            "translate_project.py",
            "translate_project.py.orig",
            "requirements.txt",
            "game-profile.json",
            "interface-translations.json",
        }
        for root, dirs, files in os_walk(input_root):
            dirs[:] = [d for d in dirs if d not in skip_names]
            for name in files:
                if name in skip_names or name.startswith((".env", ".staging-")):
                    continue
                src = Path(root) / name
                rel = src.relative_to(input_root).as_posix()
                if rel.startswith(("PTBR", ".checkpoints", ".staging")):
                    continue
                dst = staging / rel
                if not dst.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    if not _maybe_asset(src, dst):
                        shutil.copy2(src, dst)
        manifest = {
            "generation": self.generation_id,
            "formats": {name: len(paths) for name, paths in discovered.items()},
            "units": len(accepted),
            "interface": {
                "i2_mono": report.get("i2_mono", {}).get("changed", 0),
                "scene_mono": report.get("scene_mono", {}).get("patched_strings", 0),
                "dll": report.get("dll", {}).get("total", 0),
                "yarn_commands": report.get("yarn_commands", {}).get("patched", 0),
                # Guard semantics version; counts are only comparable
                # between manifests carrying the same version.
                "semantics": 2,
            },
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return report

    def publish(self, staging: Path, output_root: Path) -> None:
        output_root = Path(output_root)
        if output_root.exists():
            previous = output_root.with_name(f"PTBR.prev-{time.strftime('%Y%m%d%H%M%S')}")
            shutil.move(str(output_root), str(previous))
        shutil.move(str(staging), str(output_root))


def _asset_object_diff(
    original: Path,
    rebuilt: Path,
    allowed_textassets: set[str] | None = None,
    allowed_monobehaviours: set[int] | None = None,
) -> list[tuple[str, int]]:
    """Compare two .assets files object by object (raw content hashes).

    Returns the objects whose content differs; the caller asserts the
    list is empty or matches the intended patch targets.
    """
    import hashlib

    import UnityPy

    allowed_textassets = allowed_textassets or set()
    allowed_monobehaviours = allowed_monobehaviours or set()

    def snapshot(path: Path) -> dict[tuple[str, int], str]:
        env = UnityPy.load(str(path))
        out = {}
        for obj in env.objects:
            try:
                raw = obj.get_raw_data()
            except Exception:  # noqa: BLE001 - unreadable objects compare by absence
                raw = b""
            out[(obj.type.name, obj.path_id)] = hashlib.sha256(raw).hexdigest()
        return out

    before = snapshot(original)
    after = snapshot(rebuilt)
    diff: list[tuple[str, int]] = []
    for key, digest in before.items():
        type_name, path_id = key
        if after.get(key) == digest:
            continue
        if type_name == "TextAsset":
            name = _textasset_name(rebuilt, path_id)
            if name in allowed_textassets:
                continue
        elif type_name == "MonoBehaviour" and path_id in allowed_monobehaviours:
            continue
        diff.append(key)
    for key in after:
        if key not in before:
            diff.append(("-extra-", key[1]))
    return diff


def _textasset_name(path: Path, path_id: int) -> str:
    import UnityPy

    env = UnityPy.load(str(path))
    for obj in env.objects:
        if obj.type.name == "TextAsset" and obj.path_id == path_id:
            try:
                return str(obj.read_typetree().get("m_Name") or "")
            except Exception:  # noqa: BLE001
                return ""
    return ""


def os_walk(root: Path):
    yield from _os_walk_gen(root)


def _os_walk_gen(root: Path):
    import os

    yield from os.walk(root)


def _maybe_asset(src: Path, dst: Path) -> bool:
    """Skip game artwork and reference binaries in the player overlay."""
    del dst
    name = src.name.lower()
    return name == "manifest.json" or src.suffix.lower() == ".png" or (
        src.suffix.lower() in (".dll", ".exe") and "managed" in src.parts[-2].lower()
    )
