"""The Iron Oath PT-BR translation toolkit: command line entry point.

Run on Linux/macOS:
    python3 translate_project.py [options]

Run on Windows:
    py -3.11 translate_project.py [options]

The FreeInference profile is strict: only freeinference.org with the model
deepseek-v4-flash. A real translation run requires FREEINFERENCE_API_KEY
(process environment first, then the repository .env file).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from localization import formats
from localization.batching import BatchPlanner
from localization.checkpoint import Checkpoint
from localization.config import (
    Settings,
    load_dotenv,
    settings_from_env,
    startup_report,
    strip_diacritics,
)
from localization.models import RunStats, UnitStatus
from localization.pipeline import Pipeline
from localization.profile import load_profile, runtime_core
from localization.provider import (
    Provider,
    ProviderConfig,
    ProviderStopError,
    TransientProviderError,
)

MODES = ("translate", "review", "remake")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="translate_project.py",
        description="The Iron Oath EN -> PT-BR translation toolkit (FreeInference).",
    )
    parser.add_argument("--review", action="store_true", help="review the current translation")
    parser.add_argument("--remake", action="store_true", help="retranslate from original source")
    parser.add_argument("--input", default=".", help="input root (default: repository root)")
    parser.add_argument("--output", default="PTBR", help="output root (default: PTBR)")
    parser.add_argument(
        "--format", default="auto", choices=["auto", "hjson", "textasset_xml", "yarn"],
        help="format handler to use (default: auto)",
    )
    parser.add_argument("--source-lang", default="en")
    parser.add_argument("--target-lang", default="ptbr")
    parser.add_argument("--project-context", default="", help="authoritative context override")
    parser.add_argument("--dry-run", action="store_true", help="plan and report, call nothing")
    parser.add_argument("--validate-only", action="store_true", help="parse, rebuild, validate, no calls")
    parser.add_argument("--resume", dest="resume", action="store_true", default=None, help="resume (default)")
    parser.add_argument("--no-resume", dest="resume", action="store_false", help="fresh generation")
    parser.add_argument("--batch-size", type=int)
    parser.add_argument("--max-batch-chars", type=int)
    parser.add_argument("--max-response-size", type=int)
    parser.add_argument("--reasoning-effort", choices=["none", "low", "high", "max"])
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--retries", type=int)
    parser.add_argument("--no-stream", action="store_true")
    parser.add_argument("--translation-concurrency", type=int)
    parser.add_argument("--provider-max-concurrency", type=int)
    parser.add_argument("--min-translation-slots", type=int)
    parser.add_argument("--glossary", dest="glossary", action="store_true", default=None)
    parser.add_argument("--no-glossary", dest="glossary", action="store_false")
    parser.add_argument("--glossary-worker", dest="glossary_worker", action="store_true", default=None)
    parser.add_argument("--no-glossary-worker", dest="glossary_worker", action="store_false")
    parser.add_argument("--wait-for-glossary", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--self-test", action="store_true", help="run offline self tests and exit")
    parser.add_argument(
        "--map-file",
        default="",
        help="hand-written translations JSON ({i2, scenes, dll, units}) applied in --apply-map mode",
    )
    parser.add_argument(
        "--apply-map",
        action="store_true",
        help="publish existing translations plus the map file without provider calls",
    )
    parser.add_argument(
        "--allow-drops",
        nargs="+",
        choices=["i2_mono", "scene_mono", "dll", "yarn_commands"],
        default=[],
        help="interface counters allowed to decrease vs the published overlay (intentional reverts)",
    )
    return parser


def apply_cli(settings: Settings, args: argparse.Namespace) -> Settings:
    settings.allow_drops = list(getattr(args, "allow_drops", []) or [])
    if args.batch_size is not None:
        settings.batch_size = args.batch_size
    if args.max_batch_chars is not None:
        settings.max_batch_chars = args.max_batch_chars
    if args.max_response_size is not None:
        settings.max_response_size = args.max_response_size
    if args.reasoning_effort is not None:
        settings.reasoning_effort = args.reasoning_effort
    if args.timeout is not None:
        settings.timeout = args.timeout
    if args.retries is not None:
        settings.retries = args.retries
    if args.no_stream:
        settings.stream = False
    if args.translation_concurrency is not None:
        settings.translation_concurrency = args.translation_concurrency
    if args.provider_max_concurrency is not None:
        settings.provider_max_concurrency = args.provider_max_concurrency
    if args.min_translation_slots is not None:
        settings.min_translation_slots = args.min_translation_slots
    if args.glossary is not None:
        settings.glossary_enabled = args.glossary
    if args.glossary_worker is not None:
        settings.glossary_worker_enabled = args.glossary_worker
    if args.resume is not None:
        settings.resume = args.resume
    settings.input_root = Path(args.input).resolve()
    settings.output_root = Path(args.output).resolve()
    settings.format = args.format
    settings.source_lang = args.source_lang
    settings.target_lang = args.target_lang
    settings.project_context = args.project_context
    settings.verbose = args.verbose
    settings.apply_map = args.apply_map
    map_file = Path(args.map_file) if args.map_file else Path("interface-translations.json")
    if map_file.exists():
        # The committed hand-written map is the durable source of the
        # interface translations; default to it so every run keeps them.
        settings.map_file = str(map_file)
        settings.manual_translations = load_manual_map(map_file)
    elif args.map_file:
        # An explicitly requested map that does not exist must never be
        # silently ignored: the interface would ship in English.
        raise SystemExit(f"map file not found: {map_file}")
    if args.review and args.remake:
        raise SystemExit("--review and --remake are mutually exclusive")
    settings.mode = "review" if args.review else ("remake" if args.remake else "translate")
    return settings


def load_manual_map(path: Path) -> dict[str, str]:
    """Load the hand-written translation map and flatten it to unit ids.

    Sections: i2 {term: target}, scenes {english: target}, dll
    {type: {english: target}} and units {unit_id: target} for individual
    pipeline units (stray leftovers from earlier runs).
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    flat: dict[str, str] = {}
    for term, target in data.get("i2", {}).items():
        flat[f"i2:{term}"] = target
    for source, target in data.get("scenes", {}).items():
        flat[f"scene:{source}"] = target
    for type_name, string_map in data.get("dll", {}).items():
        for source, target in string_map.items():
            flat[f"dll:{type_name}:{source}"] = target
    for source, target in data.get("yarn_commands", {}).items():
        flat[f"ycmd:{source}"] = target
    for unit_id, target in data.get("units", {}).items():
        flat[unit_id] = target
    return flat


def _yarn_unit_id_parts(unit_id: str) -> tuple[str, str] | None:
    """Split a yarn unit id into (relpath, line index) when parseable."""
    if not unit_id.startswith("yarn:"):
        return None
    parts = unit_id.split(":")
    if parts[-1].startswith("a") and len(parts) >= 4 and parts[-2].isdigit():
        return ":".join(parts[1:-2]), parts[-2]
    if len(parts) >= 3 and parts[-1].isdigit():
        return ":".join(parts[1:-1]), parts[-1]
    return None


def _drop_legacy_shortcut_targets(
    targets: dict[str, str],
    input_root: Path,
) -> dict[str, str]:
    """Remove whole-line "[[text|node]]" artifacts stored for yarn units
    whose current extraction yields the text-only shortcut span.

    Pre-shortcut-support publications stored the raw line as the unit
    target; injecting that into the text-only span double-wraps the
    choice ("[[[[End|node]]|node]]"). Such targets are dropped so the
    line stays in English until a proper text-only target exists.
    Targets for genuinely raw lines (cost-prefixed choices such as
    "[[[Harsh]...|node]]") are left alone because those lines are still
    treated as whole-line narration units.
    """
    from localization.formats.yarn_format import _SHORTCUT_RE

    line_cache: dict[tuple[str, str], str] = {}
    out: dict[str, str] = {}
    for uid, target in targets.items():
        if target.startswith("[["):
            parsed = _yarn_unit_id_parts(uid)
            if parsed is not None:
                relpath, line = parsed
                key = (relpath, line)
                if key not in line_cache:
                    path = input_root / relpath
                    decoded = path.read_bytes().decode("utf-8-sig")
                    lines = decoded.splitlines()
                    index = int(line)
                    line_cache[key] = lines[index] if index < len(lines) else ""
                raw_line = line_cache[key].strip()
                shortcut_line = _SHORTCUT_RE.match(raw_line) is not None
                if shortcut_line or not raw_line.startswith("[["):
                    print(
                        f"dropping legacy whole-line target (unit is a text-only shortcut): {uid}",
                        file=sys.stderr,
                    )
                    continue
        out[uid] = target
    return out


def self_test() -> int:
    """Offline self tests: profile, request shape, punctuation, protection."""
    from localization.protection import (
        ProtectionError,
        protect,
        validate_punctuation,
        validate_tokens,
    )

    cases = [
        ("Você não é o líder.", []),
        ('"Você não é o líder."', []),
        ("“Você não é o líder.”", ["ASCII-fied Portuguese word detected (missing diacritics)"]),
    ]
    for text, _expected in cases:
        violations = validate_punctuation(text)
        if violations:
            print(f"punctuation gate rejected a valid case: {text!r}")
            return 1

    protected = protect("Take {0} damage!")
    assert protected.protected.count("__KEEP_") == 1, protected.protected
    validate_tokens(protected.tokens, "__KEEP_0__ dano!")
    try:
        validate_tokens(protected.tokens, "dano __KEEP_0__ __KEEP_0__")
        print("duplicated token not rejected")
        return 1
    except ProtectionError:
        pass

    from localization.config import MAX_TOKENS_CEILING, Settings

    settings = Settings()
    settings.max_response_size = MAX_TOKENS_CEILING
    settings.validate_profile()
    try:
        settings.max_response_size = MAX_TOKENS_CEILING + 1
        settings.validate_profile()
        print("max_tokens ceiling not enforced")
        return 1
    except ValueError:
        pass

    from localization.provider import build_request_body, ProviderConfig

    body = build_request_body(ProviderConfig(base_url="https://freeinference.org/v1", model="deepseek-v4-flash", api_key="x"))
    assert body == {
        "model": "deepseek-v4-flash",
        "stream": True,
        "max_tokens": 131072,
        "response_format": {"type": "json_object"},
        "thinking": {"type": "enabled"},
        "reasoning_effort": "max",
    }, body
    none_body = build_request_body(ProviderConfig(base_url="https://freeinference.org/v1", model="deepseek-v4-flash", api_key="x", reasoning_effort="none"))
    assert none_body == {
        "model": "deepseek-v4-flash",
        "stream": True,
        "max_tokens": 131072,
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
    }, none_body

    print("self tests passed")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.self_test:
        return self_test()

    settings = settings_from_env(load_dotenv())
    settings = apply_cli(settings, args)

    if args.resume is not None and not args.resume:
        settings.resume = False

    try:
        settings.validate_profile()
    except ValueError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    profile = load_profile(Path("game-profile.json"))

    if args.validate_only:
        return validate_only(settings, profile)
    if args.dry_run:
        return dry_run(settings, profile)
    if settings.apply_map:
        return apply_map_publish(settings, profile)

    if not settings.api_key:
        print(
            "FREEINFERENCE_API_KEY is not set; set it in the environment or in .env "
            "before a real run (see .env.example).",
            file=sys.stderr,
        )
        return 2

    provider = Provider(
        ProviderConfig(
            base_url=settings.base_url,
            model=settings.model,
            api_key=settings.api_key,
            reasoning_effort=settings.reasoning_effort,
            max_tokens=settings.max_response_size,
            timeout=settings.timeout,
            idle_timeout=settings.idle_timeout,
            retries=settings.retries,
            stream=settings.stream,
        )
    )
    return run_translation(settings, profile, provider)


def dry_run(settings: Settings, profile: dict) -> int:
    print(startup_report(settings))
    from localization import formats

    discovered = formats.discover(settings.input_root, settings.format)
    total = 0
    for handler_name, paths in discovered.items():
        handler = formats.HANDLERS[handler_name]
        units: list = []
        for relpath in paths:
            extracted, _ = handler.extract(settings.input_root, relpath)
            units.extend(extracted)
        print(f"{handler_name}: {len(paths)} files, {len(units)} units")
        total += len(units)
    print(f"total units: {total}")
    return 0


def validate_only(settings: Settings, profile: dict) -> int:
    print(startup_report(settings))
    from localization import formats

    discovered = formats.discover(settings.input_root, settings.format)
    problems = 0
    for handler_name, paths in discovered.items():
        handler = formats.HANDLERS[handler_name]
        if handler_name not in ("hjson", "yarn"):
            # textasset round-trips differ by the utf-8 declaration by
            # design; data files must round-trip byte-identically.
            continue
        for relpath in paths:
            rebuilt = handler.rebuild(settings.input_root, relpath, {})
            original = (settings.input_root / relpath).read_bytes()
            if rebuilt != original:
                print(f"ROUND-TRIP FAILED: {relpath}")
                problems += 1
            del rebuilt
    print("validate-only:", "FAILED" if problems else "all files round-trip byte-identically")
    return 1 if problems else 0


def apply_map_publish(settings: Settings, profile: dict) -> int:
    """Publish the existing translation plus the hand-written map.

    Reuses every translation already present in the output overlay,
    overlays the manual map (validated), refuses to publish while any
    unit of the provider-driven formats lacks a target, and never calls
    the provider.
    """
    print(startup_report(settings))
    from localization import formats
    from localization.models import RunStats, TranslationUnit, UnitStatus
    from localization.pipeline import Pipeline
    from localization.protection import ProtectionError, validate_manual_target

    manual = settings.manual_translations
    if not manual:
        print("--apply-map needs --map-file with at least one translation", file=sys.stderr)
        return 2

    pipeline = Pipeline(settings, provider=None, profile=profile, stats=RunStats())
    try:
        discovered = formats.discover(settings.input_root, settings.format)

        # Reuse the published overlay as the base translation, but only
        # where the checkpoint has no target: overlay text may carry
        # historical corruption, and adopting it wholesale let every
        # publication re-feed its own output (each run added one stray
        # leading character to Show* command args). The checkpoint holds
        # the clean provider-accepted targets.
        # Seed from the checkpoint (clean provider-accepted targets),
        # then let the published overlay fill only the gaps. Adopting
        # overlay text wholesale re-fed every publication its own
        # output (each run added one stray leading character to Show*
        # command args).
        checkpoint_accepted = _drop_legacy_shortcut_targets(
            pipeline.checkpoint.accepted_units_latest(), settings.input_root
        )
        accepted: dict[str, str] = dict(checkpoint_accepted)
        for handler_name, paths in discovered.items():
            handler = formats.HANDLERS[handler_name]
            for relpath in paths:
                try:
                    cur_units, _ = handler.extract(settings.output_root, relpath)
                except Exception:  # noqa: BLE001, S112 - partial overlays tolerated
                    continue
                for unit in cur_units:
                    if not unit.source.strip():
                        continue
                    accepted.setdefault(unit.unit_id, unit.source)
        accepted = _drop_legacy_shortcut_targets(accepted, settings.input_root)

        # Collect sources for validation and coverage checks.
        source_by_id: dict[str, str] = {}
        scene_texts: set[str] = set()
        for handler_name, paths in discovered.items():
            handler = formats.HANDLERS[handler_name]
            for relpath in paths:
                try:
                    if handler_name == "scene_mono":
                        units, _ = handler.extract(settings.input_root, relpath)
                        scene_texts.update(u.source for u in units)
                    else:
                        units, _ = handler.extract(settings.input_root, relpath)
                        for unit in units:
                            source_by_id[unit.unit_id] = unit.source
                except Exception as exc:  # noqa: BLE001
                    print(f"extraction failed for {handler_name}:{relpath}: {exc}", file=sys.stderr)
                    return 2

        # Manual targets must honor the token contract of their source;
        # the validated (escape-aligned) value is what gets published.
        invalid = 0
        for unit_id, target in list(manual.items()):
            if unit_id.startswith("dll:"):
                source = unit_id.split(":", 2)[2]
            elif unit_id.startswith("scene:"):
                source = unit_id.split(":", 1)[1]
            elif unit_id.startswith("ycmd:"):
                source = unit_id.split(":", 1)[1]
            else:
                source = source_by_id.get(unit_id, "")
            if not source:
                print(f"stale manual entry (no source found): {unit_id}", file=sys.stderr)
                invalid += 1
                continue
            try:
                manual[unit_id] = validate_manual_target(source, target)
            except ProtectionError as exc:
                print(f"manual target rejected for {unit_id}: {exc}", file=sys.stderr)
                invalid += 1
        if invalid:
            print(f"{invalid} manual entries rejected; fix the map and rerun", file=sys.stderr)
            return 2

        # No mapped string may be one the game compares at runtime:
        # translating it breaks the comparison (input actions, pronoun
        # values, save tiers, choice cost tags).
        from localization.formats.dll_format import (
            ASSEMBLY_NAME,
            MANAGED_DIRECTORY,
            find_logic_usages,
        )

        dll_path = settings.input_root / MANAGED_DIRECTORY / ASSEMBLY_NAME
        if dll_path.is_file():
            usages = find_logic_usages(dll_path, Path(settings.map_file))
            if usages:
                print(
                    f"{len(usages)} mapped strings are compared by game logic; "
                    "remove them from the map:",
                    file=sys.stderr,
                )
                for scope, en, method in usages[:15]:
                    print(f"  {scope} :: {en[:60]!r} in {method}", file=sys.stderr)
                return 2
        else:
            print("warning: input assembly not found; skipped logic-usage check", file=sys.stderr)

        # Scene map entries must exist in the scanned scenes.
        stale_scenes = [
            uid for uid in manual
            if uid.startswith("scene:") and uid.split(":", 1)[1] not in scene_texts
        ]
        if stale_scenes:
            print("scene entries with no matching m_Text:", file=sys.stderr)
            for uid in stale_scenes[:10]:
                print(f"  {uid}", file=sys.stderr)
            return 2

        # Every provider-driven and I2 unit needs a target.
        blockers = [
            uid for uid in source_by_id
            if uid not in accepted and uid not in manual
        ]
        if blockers:
            print(f"{len(blockers)} units without a target; first blockers:", file=sys.stderr)
            for uid in blockers[:10]:
                print(f"  {uid}", file=sys.stderr)
            return 2

        merged = {
            **{k: v for k, v in accepted.items() if not k.startswith(("scene:", "dll:"))},
            **{k: v for k, v in manual.items() if not k.startswith(("scene:", "dll:"))},
        }
        interface_map: dict = {
            "scenes": {
                uid.split(":", 1)[1]: target
                for uid, target in manual.items()
                if uid.startswith("scene:")
            },
            "dll": {},
            "yarn_commands": {
                uid.split(":", 1)[1]: target
                for uid, target in manual.items()
                if uid.startswith("ycmd:")
            },
        }
        for uid, target in manual.items():
            if uid.startswith("dll:"):
                _, type_name, source = uid.split(":", 2)
                interface_map["dll"].setdefault(type_name, {})[source] = target
        if settings.strip_diacritics:
            merged = {k: strip_diacritics(v) for k, v in merged.items()}
            interface_map["scenes"] = {k: strip_diacritics(v) for k, v in interface_map["scenes"].items()}
            interface_map["yarn_commands"] = {
                k: strip_diacritics(v) for k, v in interface_map["yarn_commands"].items()
            }
            interface_map["dll"] = {
                t: {k: strip_diacritics(v) for k, v in m.items()}
                for t, m in interface_map["dll"].items()
            }

        staging = Path(f".staging-{pipeline.generation_id}")
        # Register the generation so the committed manual units belong to
        # a real checkpoint generation instead of orphan rows.
        source_files: list[str] = []
        for paths in discovered.values():
            source_files.extend(paths)
        pipeline.open_generation(
            "apply-map",
            ",".join(sorted(set(source_files))),
            resume=False,
            staging=staging,
        )
        report = pipeline.build_staging(settings.input_root, merged, staging, interface_map)

        # Guard against a shrunken map silently reverting published
        # strings: never publish fewer interface patches than the
        # current overlay carries.
        previous_manifest = settings.output_root / "manifest.json"
        if previous_manifest.is_file():
            previous = json.loads(previous_manifest.read_text(encoding="utf-8")).get("interface", {})
            current = {
                "i2_mono": report.get("i2_mono", {}).get("changed", 0),
                "scene_mono": report.get("scene_mono", {}).get("patched_strings", 0),
                "dll": report.get("dll", {}).get("total", 0),
                "yarn_commands": report.get("yarn_commands", {}).get("patched", 0),
            }
            if previous.get("semantics") == 2:
                allowed = set(settings.allow_drops)
                drops = {
                    key: (previous[key], current.get(key, 0))
                    for key in ("i2_mono", "scene_mono", "dll", "yarn_commands")
                    if current.get(key, 0) < previous.get(key, 0)
                    and key not in allowed
                }
                if drops:
                    print(
                        "interface patch counts dropped vs the published overlay "
                        f"(refusing to publish): {drops}; restore the missing "
                        "interface-translations.json entries and rerun, or pass "
                        "--allow-drops with the keys being reverted on purpose",
                        file=sys.stderr,
                    )
                    return 2

        # Record the hand-written targets so future provider runs reuse them.
        saves = []
        for unit_id, target in manual.items():
            source = unit_id.split(":", 2)[2] if unit_id.startswith("dll:") else (
                unit_id.split(":", 1)[1] if unit_id.startswith("scene:") else source_by_id.get(unit_id, "")
            )
            saves.append(
                TranslationUnit(
                    unit_id=unit_id,
                    source=source,
                    source_hash=f"{len(source)}:{source[:24]}",
                    path="interface",
                    accepted_target=target,
                    status=UnitStatus.ACCEPTED,
                    generation_id=pipeline.generation_id,
                )
            )
        pipeline.checkpoint.save_units(saves, pipeline.generation_id)

        pipeline.publish(staging, settings.output_root)
        print(
            f"published {len(merged)} pipeline units; interface patches: "
            f"i2 {report.get('i2_mono', {}).get('patched', 0)}, "
            f"scenes {report.get('scene_mono', {}).get('patched_strings', 0)}, "
            f"dll {report.get('dll', {}).get('total', 0)}"
        )
        print("generation:", pipeline.generation_id)
        return 0
    finally:
        pipeline.close()


def interface_map_from_accepted(accepted: dict[str, str]) -> dict:
    """Rebuild the {scenes, dll} patch sections from stored unit ids.

    Scene and DLL patches live outside the provider unit flow, so every
    publication (manual or provider run) must reconstruct their maps
    from the accepted unit ids, otherwise a future run would drop them.
    """
    interface_map: dict = {"scenes": {}, "dll": {}, "yarn_commands": {}}
    for unit_id, target in accepted.items():
        if unit_id.startswith("scene:"):
            interface_map["scenes"][unit_id.split(":", 1)[1]] = target
        elif unit_id.startswith("dll:"):
            _, type_name, source = unit_id.split(":", 2)
            interface_map["dll"].setdefault(type_name, {})[source] = target
        elif unit_id.startswith("ycmd:"):
            interface_map["yarn_commands"][unit_id.split(":", 1)[1]] = target
    return interface_map


def run_translation(settings: Settings, profile: dict, provider: Provider) -> int:
    print(startup_report(settings))
    pipeline = Pipeline(settings, provider, profile, RunStats())
    profile_core = runtime_core(profile)
    staging = Path(f".staging-{pipeline.generation_id}")
    try:
        discovered = formats.discover(settings.input_root, settings.format)
        source_files: list[str] = []
        for paths in discovered.values():
            source_files.extend(paths)
        source_manifest = ",".join(sorted(set(source_files)))
        _, reused = pipeline.open_generation(settings.mode, source_manifest, settings.resume, staging)
        reused = _drop_legacy_shortcut_targets(reused, settings.input_root)

        total_accepted: dict[str, str] = {}
        failed: list = []
        # i2_mono, scene_mono and dll are manual-map sources: they are
        # never sent to the provider. Hand-written map targets are
        # validated against their sources and accepted directly, so
        # provider runs keep the interface patched even when the
        # checkpoint has no compatible generation yet.
        manual_only = {"i2_mono", "scene_mono", "dll"}
        manual_units: dict[str, str] = {}
        scene_census: set[str] = set()
        if settings.manual_translations:
            from localization.protection import ProtectionError, validate_manual_target

            for handler_name, paths in discovered.items():
                handler = formats.HANDLERS[handler_name]
                for relpath in paths:
                    units, _ = handler.extract(settings.input_root, relpath)
                    for unit in units:
                        manual_units[unit.unit_id] = unit.source
                        if handler_name == "scene_mono":
                            scene_census.add(unit.source)
            for uid, target in settings.manual_translations.items():
                if uid.startswith("scene:"):
                    source = uid.split(":", 1)[1]
                    if source not in scene_census:
                        print(f"stale map entry (source gone from the scenes): {uid}", file=sys.stderr)
                        return 2
                elif uid.startswith("dll:"):
                    source = uid.split(":", 2)[2]
                elif uid.startswith("ycmd:"):
                    source = uid.split(":", 1)[1]
                else:
                    source = manual_units.get(uid, "")
                    if not source:
                        print(f"stale map entry (source gone from the game files): {uid}", file=sys.stderr)
                        return 2
                try:
                    total_accepted[uid] = validate_manual_target(source, target)
                except ProtectionError as exc:
                    print(f"map target rejected for {uid}: {exc}", file=sys.stderr)
                    return 2
        for handler_name, paths in discovered.items():
            if handler_name in manual_only:
                prefix = {"i2_mono": "i2:", "scene_mono": "scene:", "dll": "dll:"}[handler_name]
                for uid, target in reused.items():
                    if uid.startswith(prefix) and uid not in total_accepted:
                        total_accepted[uid] = target
                continue
            handler = formats.HANDLERS[handler_name]
            all_units: list = []
            current_by_id: dict[str, str] = {}
            if settings.mode == "review" and settings.output_root.is_dir():
                for relpath in paths:
                    try:
                        cur_units, _ = handler.extract(settings.output_root, relpath)
                    except Exception:  # noqa: BLE001 - missing/partial output tolerated
                        cur_units = []
                    current_by_id.update({u.unit_id: u.source for u in cur_units})
            for relpath in paths:
                extracted, _ = handler.extract(settings.input_root, relpath)
                for unit in extracted:
                    unit.accepted_target = current_by_id.get(unit.unit_id, "")
                all_units.extend(extracted)
            if not all_units:
                continue
            pending = [
                u
                for u in all_units
                if u.unit_id not in reused and u.unit_id not in total_accepted
            ]
            for unit in all_units:
                if unit.unit_id in reused:
                    unit.status = UnitStatus.REUSED
            planner = BatchPlanner(settings.batch_size, settings.max_batch_chars)
            batches = planner.plan(pending)
            print(
                f"{handler_name}: {len(all_units)} units, {len(pending)} pending "
                f"(reused {len(all_units) - len(pending)}), {len(batches)} batches"
            )
            accepted, failed_units = pipeline.run_batches(batches, profile_core)
            total_accepted.update(accepted)
            failed.extend(failed_units)

        if settings.mode != "translate":
            print(f"mode {settings.mode}: publication skipped (review/remake must be published explicitly)")

        merged_accepted = {**reused, **total_accepted}
        if settings.strip_diacritics:
            # The shipped fonts lack PT-BR diacritic glyphs (boxes in game).
            # Strip only here: the checkpoint keeps the accented targets
            # so a future font fix can republish without retranslating.
            merged_accepted = {
                k: strip_diacritics(v) for k, v in merged_accepted.items()
            }
        pipeline.build_staging(
            settings.input_root,
            merged_accepted,
            staging,
            interface_map_from_accepted(merged_accepted),
        )
        pipeline.publish(staging, settings.output_root)
        stats = pipeline.stats
        print(
            f"published {len(merged_accepted)} units to {settings.output_root}; "
            f"failed {len(failed)}; batches {stats.batches}; retries {stats.retries}"
        )
        if failed:
            for unit in failed[:10]:
                print(f"  FAILED {unit.unit_id}: {unit.source[:80]!r}")
        outstanding = pipeline.glossary.outstanding()
        if outstanding:
            print(f"glossary tasks outstanding: {outstanding}")
        print("generation:", pipeline.generation_id)
        print("profile revision:", profile.get("revision"))
        return 1 if failed else 0
    except ProviderStopError as exc:
        print(f"provider stop: {exc}", file=sys.stderr)
        return 3
    except TransientProviderError as exc:
        print(f"provider failure: {exc}", file=sys.stderr)
        return 3
    finally:
        pipeline.close()


if __name__ == "__main__":
    sys.exit(main())