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
        "--format", default="auto", choices=["auto", "hjson", "textasset_xml"],
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
    return parser


def apply_cli(settings: Settings, args: argparse.Namespace) -> Settings:
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
    if args.review and args.remake:
        raise SystemExit("--review and --remake are mutually exclusive")
    settings.mode = "review" if args.review else ("remake" if args.remake else "translate")
    return settings


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
        if handler_name != "hjson":
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

        total_accepted: dict[str, str] = {}
        failed: list = []
        for handler_name, paths in discovered.items():
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
            pending = [u for u in all_units if u.unit_id not in reused]
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

        pipeline.build_staging(settings.input_root, total_accepted, staging)
        pipeline.publish(staging, settings.output_root)
        stats = pipeline.stats
        print(
            f"published {len(total_accepted)} units to {settings.output_root}; "
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