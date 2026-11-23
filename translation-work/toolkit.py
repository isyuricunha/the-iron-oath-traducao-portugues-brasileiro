#!/usr/bin/env python3
"""The Iron Oath PT-BR Localization Toolkit (Manual Workflow CLI).

Never performs machine translation, inference requests, or linguistic choices.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sys
import tempfile
import time
import zipfile

sys.dont_write_bytecode = True

WORK_DIR = Path(__file__).resolve().parent
ROOT = WORK_DIR.parent
if str(WORK_DIR) not in sys.path:
    sys.path.insert(0, str(WORK_DIR))

from context import CONFIRMED_CAMPAIGN_ACTORS
from doctor import run_doctor, verify_layered_integrity
from formats.codex_xml_format import CodexXmlFormat
from formats.dll_format import DllFormat, find_logic_usages
from formats.hjson_format import HjsonFormat
from formats.i2_mono_format import I2MonoFormat
from formats.scene_mono_format import SceneMonoFormat
from formats.textasset_xml_format import TextAssetXmlFormat
from formats.yarn_format import YarnFormat
import native
from native import Invalid, require, sha256_bytes
from storage import Store, canonical, digest, json_bytes, read_json, safe
from syntax import orthography_warnings, validate_translation

SCHEMA = 1
DEFAULT_BATCH_SIZE = 500
MANIFEST_REL = "translation-work/translations/manifest.json"
BUILD_REL = "translation-work/build-manifest.json"
SYNC_JSON_REL = "translation-work/reports/sync-latest.json"
SYNC_MD_REL = "translation-work/reports/sync-latest.md"
POLICY_REL = "translation-work/source-policy.json"
SHAPES_REL = "translation-work/engine-shapes.json"
CONFIG_REL = "translation-work/config.json"
GAME_PATH_STATE_REL = "translation-work/.state/game-path.json"
STEAM_APPID = "699330"


def lines_bytes(rows: list[dict]) -> bytes:
    return b"".join((json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8") for r in rows)


def record_source_fingerprint(rec: dict) -> str:
    payload = {
        "id": rec["id"],
        "source_file": rec["source_file"],
        "format": rec["format"],
        "key": rec["key"],
        "EN": rec["EN"],
        "speaker_gender": (rec.get("context") or {}).get("speaker_gender"),
        "referent_gender": (rec.get("context") or {}).get("referent_gender"),
        "certainty": (rec.get("context") or {}).get("certainty"),
    }
    return sha256_bytes(json_bytes(payload))


def attach_fingerprints(records: list[dict]) -> list[dict]:
    for r in records:
        r["source_fingerprint"] = record_source_fingerprint(r)
    return records


def _format_sync_md(report: dict) -> str:
    c = report["counts"]
    lines = [
        "# Relatório de Sincronização — The Iron Oath",
        "",
        f"- **Geração anterior:** `{report.get('previous_generation') or 'inicial'}`",
        f"- **Nova geração:** `{report['generation']}`",
        f"- **Validação:** `{report['validation']}`",
        "",
        "## Contagens",
        f"- Adicionadas: **{c['added']}**",
        f"- Modificadas (pendentes para revisão): **{c['modified']}**",
        f"- Removidas: **{c['removed']}**",
        f"- Movidas/reordenadas (trad. preservada): **{c['moved']}**",
        f"- Inalteradas: **{c['unchanged']}**",
        f"- Traduções PT-BR preservadas: **{c['preserved_translations']}**",
        f"- Total de entradas ativas: **{report.get('total_entries', 0)}**",
        f"- Entradas pendentes de tradução: **{report.get('pending_entries', 0)}**",
        "",
    ]
    res = report["resources"]
    if res["added"] or res["modified"] or res["removed"]:
        lines.append("## Recursos alterados")
        for p in res["added"]:
            lines.append(f"- `[adicionado]` `{p}`")
        for p in res["modified"]:
            lines.append(f"- `[modificado]` `{p}`")
        for p in res["removed"]:
            lines.append(f"- `[removido]` `{p}`")
        lines.append("")
    if report["affected_batches"]:
        lines.append("## Lotes afetados")
        lines.append(", ".join(f"`{b}`" for b in report["affected_batches"]))
        lines.append("")
    if report["changes"]:
        lines.append("## Amostra de alterações (até 100)")
        for ch in report["changes"][:100]:
            lines.append(
                f"- **{ch['kind']}** `{ch['id']}` ({ch.get('batch') or '-'}) — "
                f"EN: {ch['EN'][:100]!r}"
                + (f" | PTBR anterior: {ch['previous_PTBR'][:80]!r}" if ch.get("previous_PTBR") else "")
            )
        lines.append("")
    if report.get("warnings"):
        lines.append("## Avisos")
        for w in report["warnings"]:
            lines.append(f"- {w}")
        lines.append("")
    lines.append(f"## Próximos passos\n{report['next']}\n")
    return "\n".join(lines)


class Project:
    def __init__(self, root: Path = ROOT):
        self.root = Path(root).resolve()
        cfg_path = safe(self.root, CONFIG_REL)
        self.config = read_json(cfg_path) if cfg_path.is_file() else {}
        self.store = Store(self.root, **self.config.get("retention", {}))

    def path(self, rel: str) -> Path:
        return safe(self.root, rel)

    def policy(self) -> dict:
        p = self.path(POLICY_REL)
        return read_json(p) if p.is_file() else {}

    def shapes(self) -> dict:
        p = self.path(SHAPES_REL)
        return read_json(p) if p.is_file() else {}

    def load(self) -> tuple[dict, list[dict], dict[str, str], dict[str, str]]:
        """Load and strictly validate manifest.json and all committed batch-*.jsonl."""
        self.store.clean_state()
        manifest_path = self.path(MANIFEST_REL)
        require(manifest_path.is_file(), "NOT_EXTRACTED: missing translation-work/translations/manifest.json; run extract first")
        manifest = read_json(manifest_path)
        require(manifest.get("schema") == SCHEMA, f"Unsupported manifest schema {manifest.get('schema')!r}")
        batches = manifest.get("batches", [])
        require(
            isinstance(batches, list)
            and len(set(batches)) == len(batches)
            and all(re.fullmatch(r"batch-\d{4,}\.jsonl", b) for b in batches),
            "Invalid or duplicate batch inventory in manifest.json",
        )
        trans_dir = self.path("translation-work/translations")
        actual = sorted(p.name for p in trans_dir.glob("batch-*.jsonl"))
        require(sorted(batches) == actual, f"Batch files on disk {actual} differ from manifest {sorted(batches)}")

        records: list[dict] = []
        membership: dict[str, str] = {}
        expected: dict[str, str] = {MANIFEST_REL: digest(manifest_path) or ""}
        batch_cap = int(manifest.get("batch_size", DEFAULT_BATCH_SIZE))

        for batch_name in batches:
            rel = f"translation-work/translations/{batch_name}"
            bpath = self.path(rel)
            raw = bpath.read_bytes()
            expected[rel] = sha256_bytes(raw)
            parsed = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
            require(len(parsed) <= batch_cap, f"Batch {batch_name} exceeds cap {batch_cap}")
            for row in parsed:
                uid = row.get("id")
                require(isinstance(uid, str) and uid, f"Missing record id in {batch_name}")
                require(uid not in membership, f"Duplicate record ID {uid} across batches")
                require(isinstance(row.get("PTBR"), str), f"PTBR must be string for {uid}")
                require(
                    row.get("source_fingerprint") == record_source_fingerprint(row),
                    f"Immutable source metadata or source_fingerprint corrupted for {uid}",
                )
                membership[uid] = batch_name
            records.extend(parsed)

        require(len(records) == manifest.get("entries"), "Manifest entry count differs from batch records")
        return manifest, records, membership, expected

    def sources_digest_map(self, en_root: Path | None = None) -> dict[str, str]:
        en_root = en_root or self.path("EN")
        out: dict[str, str] = {}
        for rel in native.discover_managed_files(en_root):
            out[rel] = sha256_bytes((en_root / rel).read_bytes())
        return out

    def generation_of_sources(self, sources_map: dict[str, str]) -> str:
        return sha256_bytes(json_bytes(sources_map))

    def translation_hash(self, manifest: dict) -> str:
        return sha256_bytes(
            json_bytes(
                {
                    b: sha256_bytes(canonical(self.path(f"translation-work/translations/{b}").read_bytes()))
                    for b in manifest["batches"]
                }
            )
        )

    def _next_batch_name(self, existing: list[str]) -> str:
        nums = [int(re.search(r"\d+", b).group(0)) for b in existing] if existing else [0]
        return f"batch-{max(nums) + 1:04d}.jsonl"

    def extract(self, batch_size: int = DEFAULT_BATCH_SIZE) -> dict:
        require(1 <= batch_size <= 500, "batch_size must be between 1 and 500")
        if self.path(MANIFEST_REL).exists():
            raise Invalid("EXISTS: extract refuses to overwrite existing progress; use sync")
        with self.store.lock():
            self.store.clean_state()
            en_root = self.path("EN")
            policy = self.policy()
            records, summary = native.extract_all(en_root, policy=policy)
            attach_fingerprints(records)
            sources_map = self.sources_digest_map(en_root)
            gen = self.generation_of_sources(sources_map)

            batches: list[str] = []
            changes: dict[str, bytes | None] = {}
            for start in range(0, len(records), batch_size):
                chunk = records[start : start + batch_size]
                bname = f"batch-{len(batches) + 1:04d}.jsonl"
                batches.append(bname)
                changes[f"translation-work/translations/{bname}"] = lines_bytes(chunk)

            manifest = {
                "schema": SCHEMA,
                "game": "The Iron Oath",
                "native_version": self.shapes().get("native_version", "1.0.021"),
                "generation": gen,
                "batch_size": batch_size,
                "entries": len(records),
                "counts": summary["counts"],
                "sources": sources_map,
                "batches": batches,
            }
            changes[MANIFEST_REL] = json_bytes(manifest)

            report = {
                "previous_generation": None,
                "generation": gen,
                "resources": {"added": sorted(sources_map), "modified": [], "removed": []},
                "counts": {
                    "added": len(records),
                    "modified": 0,
                    "removed": 0,
                    "moved": 0,
                    "unchanged": 0,
                    "preserved_translations": 0,
                },
                "total_entries": len(records),
                "pending_entries": len(records),
                "affected_batches": batches,
                "changes": [],
                "validation": "passed",
                "warnings": [],
                "next": "Leia briefing-do-jogo.md e traduza os lotes em grupos gerenciáveis; nunca edite EN/ ou PTBR/ manualmente.",
            }
            changes[SYNC_JSON_REL] = json_bytes(report)
            changes[SYNC_MD_REL] = _format_sync_md(report).encode("utf-8")
            txid = self.store.commit(changes, operation="extract")
            return {"status": "extracted", "generation": gen, "entries": len(records), "batches": len(batches), "transaction": txid}

    def sync_candidate(
        self,
        candidate_root: Path,
        *,
        dry_run: bool = False,
        import_into_en: bool = False,
        allow_removed_files: bool = False,
        force: bool = False,
    ) -> dict:
        """Synchronize candidate EN source tree against accepted manifest/JSONL."""
        if not self.path(MANIFEST_REL).is_file():
            require(not import_into_en, "NOT_EXTRACTED: run extract first before update-sources")
            return self.extract()

        manifest, old_rows, membership, expected = self.load()
        policy = self.policy()
        native.verify_source_tree(candidate_root)
        cand_sources = self.sources_digest_map(candidate_root)
        cand_gen = self.generation_of_sources(cand_sources)

        # Reject translated PTBR build outputs masquerading as English originals
        build_path = self.path(BUILD_REL)
        if build_path.is_file():
            build_out_hashes = set(read_json(build_path).get("outputs", {}).values())
            for rel, sha_val in cand_sources.items():
                require(sha_val not in build_out_hashes, f"TRANSLATED_SOURCE: {rel} matches built PTBR output")

        old_sources = manifest["sources"]
        removed_files = sorted(set(old_sources) - set(cand_sources))
        if removed_files and not allow_removed_files:
            raise Invalid(f"INCOMPLETE_SOURCE: previously accepted source files vanished: {removed_files[:5]}")
        added_files = sorted(set(cand_sources) - set(old_sources))
        modified_files = sorted(k for k in set(old_sources) & set(cand_sources) if old_sources[k] != cand_sources[k])

        if not force and cand_gen == manifest["generation"] and not import_into_en:
            return {"status": "unchanged", "generation": cand_gen, "entries": len(old_rows)}

        if not force and cand_gen == manifest["generation"] and import_into_en and cand_sources == old_sources:
            return {"status": "unchanged", "generation": cand_gen, "entries": len(old_rows)}

        # Extract candidate records & fingerprints
        cand_records, summary = native.extract_all(candidate_root, policy=policy)
        attach_fingerprints(cand_records)

        old_by_id = {r["id"]: r for r in old_rows}
        batch_rows: dict[str, list[dict]] = {b: [] for b in manifest["batches"]}
        for r in old_rows:
            batch_rows[membership[r["id"]]].append(r)

        batch_cap = int(manifest.get("batch_size", DEFAULT_BATCH_SIZE))
        counts = dict.fromkeys(["added", "modified", "removed", "moved", "unchanged", "preserved_translations"], 0)
        change_items: list[dict] = []
        history_items: list[dict] = []
        affected_batches: set[str] = set()
        new_queue: list[dict] = []

        # Track old IDs replaced in-place by batch position
        updated_old_map: dict[str, dict] = {}
        seen_cand_ids: set[str] = set()
        for cand in cand_records:
            uid = cand["id"]
            seen_cand_ids.add(uid)
            prior = old_by_id.get(uid)
            if prior is None:
                counts["added"] += 1
                new_queue.append(cand)
                continue

            if prior["source_fingerprint"] == cand["source_fingerprint"]:
                # Same EN & context: preserve PTBR & batch assignment!
                merged = dict(cand)
                merged["PTBR"] = prior["PTBR"]
                if prior["row"] != cand["row"]:
                    counts["moved"] += 1
                    change_items.append(
                        {"kind": "moved", "id": uid, "batch": membership[uid], "EN": cand["EN"], "previous_PTBR": prior["PTBR"]}
                    )
                else:
                    counts["unchanged"] += 1
                if prior["PTBR"]:
                    counts["preserved_translations"] += 1
                updated_old_map[uid] = merged
            else:
                # Source text or material gender/speaker context changed: preserve prior in history and mark pending!
                counts["modified"] += 1
                bname = membership[uid]
                affected_batches.add(bname)
                merged = dict(cand)
                merged["PTBR"] = ""
                if prior["PTBR"]:
                    merged["context"] = dict(merged["context"])
                    merged["context"]["previous_EN"] = prior["EN"]
                    merged["context"]["previous_PTBR"] = prior["PTBR"]
                updated_old_map[uid] = merged
                entry_diff = {
                    "kind": "modified",
                    "id": uid,
                    "batch": bname,
                    "source_file": cand["source_file"],
                    "previous_EN": prior["EN"],
                    "EN": cand["EN"],
                    "previous_PTBR": prior["PTBR"],
                }
                change_items.append(entry_diff)
                history_items.append(entry_diff)

        # Handle removed entries without reshuffling remaining batches
        for uid, prior in old_by_id.items():
            if uid not in seen_cand_ids:
                counts["removed"] += 1
                bname = membership[uid]
                affected_batches.add(bname)
                rem_diff = {
                    "kind": "removed",
                    "id": uid,
                    "batch": bname,
                    "source_file": prior["source_file"],
                    "previous_EN": prior["EN"],
                    "EN": prior["EN"],
                    "previous_PTBR": prior["PTBR"],
                }
                change_items.append(rem_diff)
                history_items.append(rem_diff)

        # Rebuild existing batches preserving exact batch assignment (and holes on removals)
        new_batch_rows: dict[str, list[dict]] = {}
        ordered_batches = list(manifest["batches"])
        for bname in ordered_batches:
            kept: list[dict] = []
            for old_r in batch_rows[bname]:
                uid = old_r["id"]
                if uid in updated_old_map:
                    kept.append(updated_old_map[uid])
            new_batch_rows[bname] = kept

        # Append new entries to remaining capacity of last batch, then sequential new batches
        for cand in new_queue:
            target_batch = ordered_batches[-1] if ordered_batches else "batch-0001.jsonl"
            if target_batch not in new_batch_rows or len(new_batch_rows[target_batch]) >= batch_cap:
                target_batch = self._next_batch_name(ordered_batches)
                ordered_batches.append(target_batch)
                new_batch_rows[target_batch] = []
            new_batch_rows[target_batch].append(cand)
            affected_batches.add(target_batch)
            change_items.append(
                {
                    "kind": "added",
                    "id": cand["id"],
                    "batch": target_batch,
                    "source_file": cand["source_file"],
                    "EN": cand["EN"],
                }
            )

        # Compute pending count across resulting rows
        pending = sum(1 for b in ordered_batches for r in new_batch_rows[b] if not r["PTBR"].strip())
        report = {
            "previous_generation": manifest["generation"],
            "generation": cand_gen,
            "resources": {"added": added_files, "modified": modified_files, "removed": removed_files},
            "counts": counts,
            "total_entries": len(cand_records),
            "pending_entries": pending,
            "affected_batches": sorted(affected_batches),
            "changes": change_items,
            "validation": "passed",
            "warnings": [],
            "next": "Revise as entradas marcadas como pendentes em sync-latest.md e aplique com apply antes de gerar build.",
        }

        if dry_run:
            return {"status": "dry_run", **report}

        changes: dict[str, bytes | None] = {}
        # Copy updated/new EN files only when importing from external source tree
        if import_into_en:
            for rel in added_files + modified_files:
                changes[f"EN/{rel}"] = (candidate_root / rel).read_bytes()
            for rel in removed_files:
                changes[f"EN/{rel}"] = None

        # Only serialize batches whose bytes actually changed (dirty-file-only write!)
        for bname in ordered_batches:
            rel = f"translation-work/translations/{bname}"
            changes[rel] = lines_bytes(new_batch_rows[bname])

        new_manifest = {
            "schema": SCHEMA,
            "game": manifest.get("game", "The Iron Oath"),
            "native_version": self.shapes().get("native_version", manifest.get("native_version", "1.0.021")),
            "generation": cand_gen,
            "batch_size": batch_cap,
            "entries": len(cand_records),
            "counts": summary["counts"],
            "sources": cand_sources,
            "batches": ordered_batches,
        }
        changes[MANIFEST_REL] = json_bytes(new_manifest)
        changes[SYNC_JSON_REL] = json_bytes(report)
        changes[SYNC_MD_REL] = _format_sync_md(report).encode("utf-8")
        if history_items:
            changes[f"translation-work/reports/history/{cand_gen}.json"] = json_bytes(
                {"generation": cand_gen, "previous_generation": manifest["generation"], "items": history_items}
            )

        with self.store.lock():
            txid = self.store.commit(changes, expected=expected, operation="sync")
        return {"status": "synced", "transaction": txid, **report}

    def sync(
        self,
        source_root: Path | None = None,
        source_file: Path | None = None,
        *,
        dry_run: bool = False,
        force: bool = False,
    ) -> dict:
        if source_root and source_file:
            raise Invalid("--source-root and --source-file are mutually exclusive")
        if source_file:
            sf = Path(source_file).resolve()
            require(sf.is_file(), f"MISSING_SOURCE: {source_file}")
            en_root = self.path("EN")
            managed = native.discover_managed_files(en_root)
            matches = [rel for rel in managed if Path(rel).name == sf.name]
            if not matches:
                # Also match normalized relative suffix inside EN
                norm = sf.as_posix()
                matches = [rel for rel in managed if norm.endswith("/" + rel)]
            require(len(matches) == 1, f"AMBIGUOUS_OR_UNKNOWN_SOURCE_FILE: {sf.name} matched {matches}")
            target_rel = matches[0]
            with tempfile.TemporaryDirectory(prefix="tio-syncfile-") as tmp:
                tmp_root = Path(tmp)
                shutil.copytree(en_root, tmp_root, symlinks=False)
                dst = safe(tmp_root, target_rel)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(sf, dst)
                return self.sync_candidate(tmp_root, dry_run=dry_run, import_into_en=True, force=force)

        if source_root:
            cand = self._resolve_game_root_candidate(Path(source_root))
            return self.sync_candidate(cand, dry_run=dry_run, import_into_en=True, force=force)

        return self.sync_candidate(self.path("EN"), dry_run=dry_run, import_into_en=False, force=force)

    def _resolve_game_root_candidate(self, given: Path) -> Path:
        """Accept either game root (containing The Iron Oath_Data) or The Iron Oath_Data directly (when unambiguous)."""
        resolved = given.resolve()
        require(resolved.is_dir(), f"GAME_ROOT_NOT_FOUND: {given}")
        # Reject symlink escapes or pointing inside repository PTBR/EN directly as --game-root
        repo_root = self.root.resolve()
        require(
            resolved != repo_root and not resolved.is_relative_to(repo_root / "PTBR"),
            "INVALID_GAME_ROOT: --game-root must point to an external game installation, not the PTBR overlay",
        )
        if (resolved / "The Iron Oath_Data").is_dir():
            return resolved
        if resolved.name == "The Iron Oath_Data" and (resolved / "resources.assets").is_file():
            return resolved.parent
        raise Invalid(f"INVALID_GAME_ROOT: {given} does not contain 'The Iron Oath_Data'")

    def _is_valid_english_install(self, root_dir: Path) -> tuple[bool, str]:
        try:
            native.verify_source_tree(root_dir)
        except Invalid as exc:
            return False, str(exc)
        # Reject translated PTBR overlays installed over game directory
        build_path = self.path(BUILD_REL)
        if build_path.is_file():
            build_outs = set(read_json(build_path).get("outputs", {}).values())
            for rel in native.REQUIRED_CORE_FILES:
                if sha256_bytes((root_dir / rel).read_bytes()) in build_outs:
                    return False, f"{rel} matches translated PTBR build output"
        # Spot-check I2 main menu label isn't already translated
        try:
            recs, _ = I2MonoFormat.extract(root_dir, I2MonoFormat.ASSET_PATH)
            by_k = {r["key"]: r["EN"] for r in recs}
            if by_k.get("tio_ACC") and by_k.get("tio_ACC") != "Accuracy":
                return False, "I2Languages appears already modified/translated"
        except Exception as exc:
            return False, f"Failed reading I2Languages: {exc}"
        return True, "ok"

    def _discover_installed_game_roots(self) -> list[Path]:
        """Bounded discovery across machine-local cache, Steam VDF manifests, and standard GOG paths."""
        candidates: list[Path] = []
        seen: set[Path] = set()

        def add_cand(p: Path) -> None:
            try:
                rp = p.resolve()
            except OSError:
                return
            if rp not in seen and rp.is_dir() and (rp / "The Iron Oath_Data").is_dir():
                seen.add(rp)
                candidates.append(rp)

        # 1. Machine-local remembered path (gitignored)
        state_path = self.path(GAME_PATH_STATE_REL)
        if state_path.is_file():
            try:
                saved = json.loads(state_path.read_text(encoding="utf-8")).get("game_root")
                if saved:
                    add_cand(Path(saved))
            except Exception:
                pass

        # 2. Bounded Steam libraryfolders.vdf manifests
        steam_roots: list[Path] = []
        if os.name == "nt":
            for env_var in ("ProgramFiles(x86)", "ProgramFiles"):
                base = os.environ.get(env_var)
                if base:
                    steam_roots.append(Path(base) / "Steam")
            for drive in ("C:", "D:", "E:", "F:", "G:"):
                steam_roots.append(Path(f"{drive}/SteamLibrary"))
                steam_roots.append(Path(f"{drive}/GOG Games/The Iron Oath"))
        else:
            home = Path.home()
            steam_roots.extend([
                home / ".local/share/Steam",
                home / ".steam/steam",
                home / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
            ])
            add_cand(home / "GOG Games/The Iron Oath")

        for sr in steam_roots:
            if sr.name == "The Iron Oath":
                add_cand(sr)
                continue
            vdf = sr / "steamapps/libraryfolders.vdf"
            lib_dirs = [sr]
            if vdf.is_file():
                text = vdf.read_text(encoding="utf-8", errors="ignore")
                for m in re.finditer(r'"path"\s+"([^"]+)"', text):
                    lib_dirs.append(Path(m.group(1).replace("\\\\", "\\")))
            for lib in lib_dirs:
                acf = lib / f"steamapps/appmanifest_{STEAM_APPID}.acf"
                if acf.is_file():
                    acf_txt = acf.read_text(encoding="utf-8", errors="ignore")
                    inst = re.search(r'"installdir"\s+"([^"]+)"', acf_txt)
                    if inst:
                        add_cand(lib / "steamapps/common" / inst.group(1))
                add_cand(lib / "steamapps/common/The Iron Oath")

        return candidates

    def update_sources(self, game_root: Path | None = None, *, dry_run: bool = False) -> dict:
        if game_root is not None:
            cand = self._resolve_game_root_candidate(Path(game_root))
            ok, reason = self._is_valid_english_install(cand)
            require(ok, f"INVALID_GAME_ROOT ({cand}): {reason}")
            selected = cand
        else:
            raw_cands = self._discover_installed_game_roots()
            valid: list[Path] = []
            reasons: list[str] = []
            for c in raw_cands:
                ok, reason = self._is_valid_english_install(c)
                if ok:
                    valid.append(c)
                else:
                    reasons.append(f"{c}: {reason}")
            if len(valid) != 1:
                hint = (
                    "python translation-work/toolkit.py update-sources --game-root \"/path/to/The Iron Oath\""
                )
                detail = "; ".join(reasons) if reasons else "no local installation detected"
                raise Invalid(
                    f"GAME_ROOT_UNDETECTED (candidates={len(valid)}): {detail}. Provide explicit installation root: {hint}"
                )
            selected = valid[0]

        result = self.sync_candidate(selected, dry_run=dry_run, import_into_en=True)
        result["game_root"] = str(selected)
        if not dry_run:
            state_file = self.path(GAME_PATH_STATE_REL)
            state_file.parent.mkdir(parents=True, exist_ok=True)
            state_file.write_text(json.dumps({"game_root": str(selected)}, indent=2) + "\n", encoding="utf-8")
        return result

    def apply(self, payload_path: Path, *, replace: bool = False) -> dict:
        manifest, rows, membership, expected = self.load()
        data = read_json(Path(payload_path))
        require(isinstance(data, dict), "Payload must be a JSON object")
        require(data.get("generation") == manifest["generation"], "WRONG_GENERATION: payload generation does not match active manifest")
        entries = data.get("entries")
        require(isinstance(entries, list), "Payload requires 'entries' list")

        by_id = {r["id"]: r for r in rows}
        seen_payload_ids: set[str] = set()
        for item in entries:
            require(isinstance(item, dict), "Payload entry must be an object")
            uid = item.get("id")
            require(isinstance(uid, str) and uid in by_id, f"UNKNOWN_ID: {uid}")
            require(uid not in seen_payload_ids, f"DUPLICATE_ID in payload: {uid}")
            seen_payload_ids.add(uid)
            row = by_id[uid]
            fp = item.get("source_fingerprint")
            if fp is not None:
                require(fp == row["source_fingerprint"], f"FINGERPRINT_MISMATCH for {uid}")
            require(isinstance(item.get("PTBR"), str), f"PTBR must be string for {uid}")

        batch_rows: dict[str, list[dict]] = {b: [] for b in manifest["batches"]}
        for r in rows:
            batch_rows[membership[r["id"]]].append(r)

        accepted_ids: list[str] = []
        rejected: list[dict] = []
        touched_batches: set[str] = set()
        replaced_history: list[dict] = []

        for item in entries:
            uid = item["id"]
            row = by_id[uid]
            new_pt = item["PTBR"]
            if row["PTBR"] and row["PTBR"] != new_pt and not replace:
                rejected.append({"id": uid, "reason": "OVERWRITE_DENIED: entry already has PTBR (pass --replace to update)"})
                continue
            errs = validate_translation(row["EN"], new_pt)
            if errs:
                rejected.append({"id": uid, "reason": "; ".join(errs)})
                continue
            if row["PTBR"] == new_pt:
                accepted_ids.append(uid)
                continue
            if row["PTBR"] and replace:
                replaced_history.append({"id": uid, "previous_PTBR": row["PTBR"], "new_PTBR": new_pt})
            row["PTBR"] = new_pt
            touched_batches.add(membership[uid])
            accepted_ids.append(uid)

        changes: dict[str, bytes | None] = {}
        for bname in sorted(touched_batches):
            changes[f"translation-work/translations/{bname}"] = lines_bytes(batch_rows[bname])

        if changes:
            with self.store.lock():
                txid = self.store.commit(changes, expected=expected, operation="apply")
        else:
            txid = None

        result = {
            "status": "partial_error" if rejected else "ok",
            "applied": len(accepted_ids),
            "rejected": len(rejected),
            "rejected_details": rejected,
            "touched_batches": sorted(touched_batches),
            "transaction": txid,
        }
        return result

    def check(self, *, check_dll_usages: bool = True) -> dict:
        manifest, rows, _membership, _expected = self.load()
        en_root = self.path("EN")
        # Check EN files integrity against manifest
        for rel, expected_sha in manifest["sources"].items():
            fpath = en_root / rel
            require(fpath.is_file(), f"MISSING_SOURCE: EN/{rel}")
            require(sha256_bytes(fpath.read_bytes()) == expected_sha, f"SOURCE_CHANGED: EN/{rel} modified; run sync")

        completed = 0
        pending = 0
        syntax_errors: list[dict] = []
        accent_warnings = 0
        dll_translated: dict[str, dict[str, str]] = {}

        for row in rows:
            pt = row["PTBR"]
            if not pt.strip():
                pending += 1
                continue
            completed += 1
            errs = validate_translation(row["EN"], pt)
            if errs:
                syntax_errors.append({"id": row["id"], "errors": errs})
            if orthography_warnings(pt):
                accent_warnings += 1
            if row["format"] == "dll":
                dll_translated.setdefault(row["context"]["type"], {})[row["EN"]] = pt

        require(not syntax_errors, f"SYNTAX_VALIDATION_FAILED ({len(syntax_errors)}): {syntax_errors[:5]}")
        if check_dll_usages and dll_translated:
            dll_path = en_root / DllFormat.ASSEMBLY_REL
            if dll_path.is_file():
                usages = find_logic_usages(dll_path, dll_translated)
                require(not usages, f"LOGIC_USAGE_DETECTED in Assembly-CSharp.dll: {usages[:5]}")

        return {
            "status": "ok",
            "generation": manifest["generation"],
            "entries": len(rows),
            "completed": completed,
            "pending": pending,
            "batches": len(manifest["batches"]),
            "unaccented_ptbr_hints": accent_warnings,
        }

    def status(self) -> dict:
        storage = self.store.stats()
        if storage["protected"]:
            return {"recovery_required": True, "next": "recover", "storage": storage}
        manifest, rows, _membership, _expected = self.load()
        completed = sum(1 for r in rows if r["PTBR"].strip())
        en_root = self.path("EN")
        source_changed = any(
            (not (en_root / rel).is_file()) or (sha256_bytes((en_root / rel).read_bytes()) != expected_sha)
            for rel, expected_sha in manifest["sources"].items()
        )
        output_status = "absent"
        if self.path(BUILD_REL).exists() or self.path("PTBR").exists():
            try:
                self.verify_output()
                output_status = "verified"
            except (Invalid, OSError, KeyError, ValueError):
                output_status = "stale-or-invalid"

        next_pending = next((r["id"] for r in rows if not r["PTBR"].strip()), None)
        return {
            "game": "The Iron Oath",
            "generation": manifest["generation"],
            "entries": len(rows),
            "completed": completed,
            "pending": len(rows) - completed,
            "batches": len(manifest["batches"]),
            "PTBR": output_status,
            "source_changed": source_changed,
            "next_pending": next_pending,
            "storage": storage,
        }

    def diff(self, source_root: Path | None = None, source_file: Path | None = None) -> dict:
        if source_root or source_file:
            return self.sync(source_root=source_root, source_file=source_file, dry_run=True)
        en_root = self.path("EN")
        manifest, _rows, _mem, _exp = self.load()
        if self.generation_of_sources(self.sources_digest_map(en_root)) != manifest["generation"]:
            return self.sync(dry_run=True)
        rep = self.path(SYNC_JSON_REL)
        require(rep.is_file(), "NO_DIFF_REPORT: sync-latest.json not found")
        return read_json(rep)

    def build(self, *, keep_diacritics: bool = False) -> dict:
        verify_layered_integrity(self.root)
        manifest, rows, _membership, expected = self.load()
        en_root = self.path("EN")
        for rel, expected_sha in manifest["sources"].items():
            fpath = en_root / rel
            require(fpath.is_file(), f"MISSING_SOURCE: EN/{rel}")
            require(sha256_bytes(fpath.read_bytes()) == expected_sha, f"SOURCE_CHANGED: EN/{rel} modified; run sync")

        pending = [r["id"] for r in rows if not r["PTBR"].strip()]
        if pending:
            raise Invalid(f"INCOMPLETE_TRANSLATION: {len(pending)} entries pending (first: {pending[0]})")

        shapes = self.shapes()
        strip_accents = False if keep_diacritics else bool(shapes.get("strip_diacritics_on_build", True))
        policy = self.policy()

        with self.store.lock():
            with tempfile.TemporaryDirectory(prefix="tio-build-") as tmp:
                staging = Path(tmp) / "PTBR"
                outputs, build_report = native.build_deployable_tree(
                    en_root, rows, staging, policy=policy, strip_accents=strip_accents
                )

                # Prepare atomic commit over PTBR/ + translation-work/build-manifest.json
                changes: dict[str, bytes | None] = {}
                ptbr_dir = self.path("PTBR")
                if ptbr_dir.is_dir():
                    for existing in sorted(ptbr_dir.rglob("*")):
                        if existing.is_file():
                            rel_inside = existing.relative_to(ptbr_dir).as_posix()
                            if rel_inside not in outputs:
                                changes[f"PTBR/{rel_inside}"] = None
                for rel, sha_val in outputs.items():
                    del sha_val
                    changes[f"PTBR/{rel}"] = (staging / rel).read_bytes()

                runtime = read_json(self.path("translation-work/runtime-support.json"))
                build_manifest = {
                    "schema": SCHEMA,
                    "complete": True,
                    "game": "The Iron Oath",
                    "native_version": shapes.get("native_version", "1.0.021"),
                    "generation": manifest["generation"],
                    "source_sha256": manifest["generation"],
                    "translation_sha256": self.translation_hash(manifest),
                    "toolchain_sha256": runtime["toolchain_sha256"],
                    "entries": len(rows),
                    "completed": len(rows),
                    "strip_diacritics": strip_accents,
                    "counts": manifest["counts"],
                    "outputs": outputs,
                    "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
                changes[BUILD_REL] = json_bytes(build_manifest)
                txid = self.store.commit(changes, expected=expected, operation="build")
                return {
                    "status": "built",
                    "files": len(outputs),
                    "entries": len(rows),
                    "strip_diacritics": strip_accents,
                    "transaction": txid,
                    "report": build_report,
                }

    def verify_output(self) -> dict:
        """Fast, read-only CI guard validating build-manifest.json against EN, JSONL, toolchain, and PTBR/."""
        runtime = verify_layered_integrity(self.root)
        manifest, rows, _membership, _expected = self.load()
        build_path = self.path(BUILD_REL)
        require(build_path.is_file(), "MISSING_BUILD_MANIFEST: run build first")
        receipt = read_json(build_path)
        require(receipt.get("schema") == SCHEMA and receipt.get("complete") is True, "INVALID_BUILD_MANIFEST")

        # Validate EN source fingerprints match manifest
        en_root = self.path("EN")
        for rel, expected_sha in manifest["sources"].items():
            fpath = en_root / rel
            require(fpath.is_file(), f"MISSING_SOURCE: EN/{rel}")
            require(sha256_bytes(fpath.read_bytes()) == expected_sha, f"SOURCE_CHANGED: EN/{rel}")

        require(receipt.get("source_sha256") == manifest["generation"], "STALE_BUILD: source generation changed")
        require(receipt.get("translation_sha256") == self.translation_hash(manifest), "STALE_BUILD: JSONL translations changed")
        require(receipt.get("toolchain_sha256") == runtime["toolchain_sha256"], "STALE_BUILD: toolchain changed")

        completed = 0
        for row in rows:
            pt = row["PTBR"]
            require(isinstance(pt, str) and bool(pt.strip()), f"INCOMPLETE_TRANSLATION: {row['id']} is pending")
            errs = validate_translation(row["EN"], pt)
            require(not errs, f"SYNTAX_ERROR in {row['id']}: {'; '.join(errs)}")
            completed += 1
        require(
            receipt.get("entries") == len(rows) and receipt.get("completed") == completed,
            "BUILD_COUNT_MISMATCH",
        )

        ptbr_dir = self.path("PTBR")
        require(ptbr_dir.is_dir(), "MISSING_PTBR_DIR: PTBR/ does not exist")
        expected_outputs = receipt.get("outputs", {})
        require(isinstance(expected_outputs, dict) and bool(expected_outputs), "EMPTY_OUTPUT_MANIFEST")

        actual_files: set[str] = set()
        for p in sorted(ptbr_dir.rglob("*")):
            if p.is_file():
                safe(self.root, p.relative_to(self.root).as_posix())
                rel = p.relative_to(ptbr_dir).as_posix()
                require(
                    rel.startswith("The Iron Oath_Data/"),
                    f"UNSAFE_OR_UNEXPECTED_PTBR_FILE: {rel} belongs outside PTBR/",
                )
                actual_files.add(rel)

        require(actual_files == set(expected_outputs), f"PTBR_INVENTORY_MISMATCH: missing={sorted(set(expected_outputs)-actual_files)[:3]}, extra={sorted(actual_files-set(expected_outputs))[:3]}")
        for rel, expected_sha in expected_outputs.items():
            actual_sha = sha256_bytes((ptbr_dir / rel).read_bytes())
            require(actual_sha == expected_sha, f"PTBR_HASH_MISMATCH: {rel}")

        return {"status": "verified", "files": len(actual_files), "entries": completed, "generation": manifest["generation"]}

    def package(self, zip_out: Path | None = None) -> dict:
        verified = self.verify_output()
        receipt = read_json(self.path(BUILD_REL))
        ptbr_dir = self.path("PTBR")
        out_path = Path(zip_out) if zip_out else self.path("PTBR.zip")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_path.with_name(out_path.name + ".tmp")
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for rel in sorted(receipt["outputs"]):
                info = zipfile.ZipInfo(rel, date_time=(2023, 11, 2, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                zf.writestr(info, (ptbr_dir / rel).read_bytes())
        os.replace(tmp, out_path)
        return {"status": "packaged", "zip": str(out_path), "files": verified["files"], "sha256": digest(out_path)}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="toolkit.py", description="The Iron Oath PT-BR Localization Toolkit")
    sub = p.add_subparsers(dest="command", required=True)

    ex = sub.add_parser("extract", help="Initial extraction from EN/ into 500-entry JSONL batches")
    ex.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)

    sy = sub.add_parser("sync", help="Reconcile EN/ or supplied original source against accepted generation")
    sy.add_argument("--source-root", type=Path)
    sy.add_argument("--source-file", type=Path)
    sy.add_argument("--dry-run", action="store_true")
    sy.add_argument("--force", action="store_true")

    us = sub.add_parser("update-sources", help="Import verified original game files from local install or --game-root and sync")
    us.add_argument("--game-root", type=Path)
    us.add_argument("--dry-run", action="store_true")

    up = sub.add_parser("update", help="Alias for update-sources")
    up.add_argument("--game-root", type=Path)
    up.add_argument("--dry-run", action="store_true")

    ch = sub.add_parser("check", help="Validate JSONL batches, source integrity, syntax, and DLL logic safety")
    ch.add_argument("--skip-dll-usages", action="store_true")

    df = sub.add_parser("diff", help="Read-only view of latest sync report or candidate source diff")
    df.add_argument("--source-root", type=Path)
    df.add_argument("--source-file", type=Path)

    sub.add_parser("status", help="Show progress, pending work, output status, and storage usage")
    sub.add_parser("resume", help="Alias for status")

    ap = sub.add_parser("apply", help="Apply PTBR-only JSON payload transactionally to affected batches")
    ap.add_argument("--payload", type=Path, required=True)
    ap.add_argument("--replace", action="store_true")

    bd = sub.add_parser("build", help="Validate 100%% completion and atomically build deployable PTBR/")
    bd.add_argument("--keep-diacritics", action="store_true")

    sub.add_parser("verify-output", help="Fast read-only CI verification of PTBR/ and build-manifest.json")

    pk = sub.add_parser("package", help="Verify and package PTBR/ contents into PTBR.zip")
    pk.add_argument("--output", type=Path)

    sub.add_parser("doctor", help="Read-only ARM64/Windows preflight check")

    rc = sub.add_parser("recover", help="Recover interrupted transactions")
    rc.add_argument("--break-lock", action="store_true")

    rb = sub.add_parser("rollback", help="Roll back latest completed transaction snapshot")
    rb.add_argument("--snapshot", type=str)

    gc_p = sub.add_parser("gc", help="Prune eligible completed local transaction snapshots")
    gc_p.add_argument("--dry-run", action="store_true")

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    proj = Project(ROOT)
    cmd = args.command
    try:
        if cmd == "extract":
            out = proj.extract(batch_size=args.batch_size)
        elif cmd == "sync":
            out = proj.sync(source_root=args.source_root, source_file=args.source_file, dry_run=args.dry_run, force=args.force)
        elif cmd in ("update-sources", "update"):
            out = proj.update_sources(game_root=args.game_root, dry_run=args.dry_run)
        elif cmd == "check":
            out = proj.check(check_dll_usages=not args.skip_dll_usages)
        elif cmd == "diff":
            out = proj.diff(source_root=args.source_root, source_file=args.source_file)
        elif cmd in ("status", "resume"):
            out = proj.status()
        elif cmd == "apply":
            out = proj.apply(args.payload, replace=args.replace)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 1 if out["rejected"] else 0
        elif cmd == "build":
            out = proj.build(keep_diacritics=args.keep_diacritics)
        elif cmd == "verify-output":
            out = proj.verify_output()
        elif cmd == "package":
            out = proj.package(zip_out=args.output)
        elif cmd == "doctor":
            out = run_doctor(ROOT)
        elif cmd == "recover":
            if args.break_lock:
                proj.store.break_lock()
            with proj.store.lock():
                recovered = proj.store.recover()
            out = {"status": "recovered", "transactions": recovered}
        elif cmd == "rollback":
            with proj.store.lock():
                txid = proj.store.rollback(snapshot_id=args.snapshot)
            out = {"status": "rolled_back", "transaction": txid}
        elif cmd == "gc":
            if args.dry_run:
                out = {"status": "dry_run", "reclaimable": proj.store.gc(dry=True)}
            else:
                with proj.store.lock():
                    removed = proj.store.gc(dry=False)
                out = {"status": "pruned", "removed": removed}
        else:
            raise Invalid(f"Unknown command: {cmd}")
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    except (Invalid, ValueError, KeyError, json.JSONDecodeError, OSError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
