"""Native format orchestration, lossless verification, and staging builder for The Iron Oath."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import unicodedata

from formats.codex_xml_format import CodexXmlFormat
from formats.dll_format import DllFormat, find_logic_usages
from formats.hjson_format import HjsonFormat
from formats.i2_mono_format import I2MonoFormat
from formats.scene_mono_format import SceneMonoFormat
from formats.textasset_xml_format import TextAssetXmlFormat
from formats.unity_assets import CLASS_ID_MONOBEHAVIOUR, CLASS_ID_TEXT_ASSET, load_asset, parse_serialized_file, read_text_asset
from formats.yarn_format import YarnFormat
from syntax import assert_valid_translation, validate_translation


class Invalid(ValueError):
    """Raised when validation, integrity, or safety preconditions fail."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise Invalid(message)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def strip_diacritics(text: str) -> str:
    """Decompose and strip combining marks for pixel bitmap fonts lacking Latin accents."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


REQUIRED_CORE_FILES = (
    "The Iron Oath_Data/resources.assets",
    "The Iron Oath_Data/Managed/Assembly-CSharp.dll",
    "The Iron Oath_Data/StreamingAssets/Data/QuestData/TioQuests.yarn.txt",
    "The Iron Oath_Data/StreamingAssets/Data/Codex/codex.xml",
    "The Iron Oath_Data/StreamingAssets/Data/WorldSim/codex.json",
)


def discover_managed_files(input_root: Path) -> list[str]:
    """Return sorted deployment-relative paths of all managed localization source files."""
    files: set[str] = set()
    files.update(HjsonFormat.discover(input_root))
    files.update(YarnFormat.discover(input_root))
    files.update(CodexXmlFormat.discover(input_root))
    files.update(TextAssetXmlFormat.discover(input_root))
    files.update(I2MonoFormat.discover(input_root))
    files.update(SceneMonoFormat.discover(input_root))
    files.update(DllFormat.discover(input_root))
    return sorted(files)


def verify_source_tree(input_root: Path) -> None:
    """Verify input_root is a genuine, complete The Iron Oath EN data tree."""
    for rel in REQUIRED_CORE_FILES:
        require((input_root / rel).is_file(), f"INCOMPLETE_SOURCE: missing required game file {rel}")
    require(bool(HjsonFormat.discover(input_root)), "INCOMPLETE_SOURCE: no HJSON files discovered")
    require(bool(TextAssetXmlFormat.discover(input_root)), "INVALID_SOURCE: LocalizationText missing from resources.assets")
    require(bool(I2MonoFormat.discover(input_root)), "INVALID_SOURCE: I2Languages missing from resources.assets")


def extract_all(input_root: Path, policy: dict | None = None) -> tuple[list[dict], dict]:
    """Extract all translatable records in deterministic order from EN tree."""
    verify_source_tree(input_root)
    policy = policy or {}
    all_records: list[dict] = []
    counts: dict[str, int] = {}

    for rel in HjsonFormat.discover(input_root):
        recs, _ = HjsonFormat.extract(input_root, rel, policy=policy)
        all_records.extend(recs)
        counts["hjson"] = counts.get("hjson", 0) + len(recs)

    for rel in YarnFormat.discover(input_root):
        recs, _ = YarnFormat.extract(input_root, rel)
        all_records.extend(recs)
        counts["yarn"] = counts.get("yarn", 0) + len(recs)

    for rel in CodexXmlFormat.discover(input_root):
        recs, _ = CodexXmlFormat.extract(input_root, rel)
        all_records.extend(recs)
        counts["codex_xml"] = counts.get("codex_xml", 0) + len(recs)

    for rel in TextAssetXmlFormat.discover(input_root):
        recs, _ = TextAssetXmlFormat.extract(input_root, rel)
        all_records.extend(recs)
        counts["textasset_xml"] = counts.get("textasset_xml", 0) + len(recs)

    for rel in I2MonoFormat.discover(input_root):
        recs, _ = I2MonoFormat.extract(input_root, rel, policy=policy)
        all_records.extend(recs)
        counts["i2_mono"] = counts.get("i2_mono", 0) + len(recs)

    scene_files = SceneMonoFormat.discover(input_root)
    if scene_files:
        recs = SceneMonoFormat.extract_all(input_root, scene_files, policy=policy)
        all_records.extend(recs)
        counts["scene_mono"] = len(recs)

    for rel in DllFormat.discover(input_root):
        recs, _ = DllFormat.extract(input_root, rel, policy=policy)
        all_records.extend(recs)
        counts["dll"] = counts.get("dll", 0) + len(recs)

    # Re-index global deterministic row numbers and ensure uniqueness
    seen_ids: set[str] = set()
    for idx, rec in enumerate(all_records):
        uid = rec["id"]
        require(uid not in seen_ids, f"DUPLICATE_ID extracted: {uid}")
        seen_ids.add(uid)
        rec["row"] = idx

    return all_records, {"counts": counts, "total": len(all_records)}


def validate_text(source: str, target: str) -> None:
    assert_valid_translation(source, target)


def _asset_object_diff(
    before_bytes: bytes,
    after_bytes: bytes,
    *,
    allowed_textassets: set[str] | None = None,
    allowed_monobehaviours: set[int] | None = None,
) -> list[tuple[int, int]]:
    allowed_textassets = allowed_textassets or set()
    allowed_monobehaviours = allowed_monobehaviours or set()
    a1 = parse_serialized_file(before_bytes)
    a2 = parse_serialized_file(after_bytes)
    m1 = {(o.class_id, o.path_id): sha256_bytes(o.raw) for o in a1.objects}
    m2 = {(o.class_id, o.path_id): sha256_bytes(o.raw) for o in a2.objects}
    raw2 = {o.path_id: o.raw for o in a2.objects}

    diff: list[tuple[int, int]] = []
    for key, d1 in m1.items():
        cid, pid = key
        if m2.get(key) == d1:
            continue
        if cid == CLASS_ID_TEXT_ASSET:
            name, _s, _t = read_text_asset(raw2[pid])
            if name in allowed_textassets:
                continue
        elif cid == CLASS_ID_MONOBEHAVIOUR and pid in allowed_monobehaviours:
            continue
        diff.append(key)
    for key in m2:
        if key not in m1:
            diff.append(key)
    return diff


def build_deployable_tree(
    en_root: Path,
    rows: list[dict],
    staging_dir: Path,
    policy: dict | None = None,
    *,
    strip_accents: bool = True,
) -> tuple[dict[str, str], dict]:
    """Build complete deployable PTBR/The Iron Oath_Data tree in staging_dir.

    Only outputs the localized/patched native game files needed for installation.
    Returns ({deploy_relpath: sha256}, report).
    """
    verify_source_tree(en_root)
    policy = policy or {}
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)

    # Validate every row first before writing anything
    by_id: dict[str, str] = {}
    by_format: dict[str, dict[str, str]] = {}
    for row in rows:
        en = row["EN"]
        pt = row["PTBR"]
        require(isinstance(pt, str) and bool(pt.strip()), f"PENDING_TRANSLATION: {row['id']}")
        errs = validate_translation(en, pt)
        require(not errs, f"SYNTAX_ERROR in {row['id']}: {'; '.join(errs)}")
        final_pt = strip_diacritics(pt) if strip_accents else pt
        by_id[row["id"]] = final_pt
        by_format.setdefault(row["format"], {})[row["id"]] = final_pt

    outputs: dict[str, str] = {}
    report: dict = {"strip_diacritics": strip_accents, "formats": {}}

    def write_output(rel: str, data: bytes) -> None:
        dst = staging_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        outputs[rel] = sha256_bytes(data)

    # 1. HJSON files (only emit files that have translatable units or differ from EN)
    hjson_changed = 0
    for rel in HjsonFormat.discover(en_root):
        recs, _ = HjsonFormat.extract(en_root, rel, policy=policy)
        if not recs:
            continue
        rebuilt = HjsonFormat.rebuild(en_root, rel, by_id, policy=policy)
        if rebuilt != (en_root / rel).read_bytes():
            write_output(rel, rebuilt)
            hjson_changed += 1
    report["formats"]["hjson"] = {"written": hjson_changed, "units": len(by_format.get("hjson", {}))}

    # 2. Yarn scripts
    yarn_written = 0
    for rel in YarnFormat.discover(en_root):
        rebuilt = YarnFormat.rebuild(en_root, rel, by_id)
        write_output(rel, rebuilt)
        yarn_written += 1
    report["formats"]["yarn"] = {"written": yarn_written, "units": len(by_format.get("yarn", {}))}

    # 3. Codex XML
    codex_written = 0
    for rel in CodexXmlFormat.discover(en_root):
        rebuilt = CodexXmlFormat.rebuild(en_root, rel, by_id)
        write_output(rel, rebuilt)
        codex_written += 1
    report["formats"]["codex_xml"] = {"written": codex_written, "units": len(by_format.get("codex_xml", {}))}

    # 4. resources.assets (LocalizationText + I2Languages in one surgical rebuild)
    res_rel = I2MonoFormat.ASSET_PATH
    res_orig = (en_root / res_rel).read_bytes()
    xml_map = by_format.get("textasset_xml", {})
    new_xml = TextAssetXmlFormat.rebuild_xml(en_root, res_rel, xml_map)
    mismatches = TextAssetXmlFormat.validate_str(new_xml, xml_map)
    require(mismatches == 0, f"LocalizationText rebuild had {mismatches} mismatches")
    res_after_xml = TextAssetXmlFormat.patch_asset_bytes(res_orig, new_xml)

    i2_translations = {
        uid.split(":", 1)[1]: val
        for uid, val in by_format.get("i2_mono", {}).items()
        if uid.startswith("i2:")
    }
    res_final, i2_rep = I2MonoFormat.patch_asset_bytes(res_after_xml, i2_translations)
    i2_problems = I2MonoFormat.validate_bytes(res_final, i2_translations)
    require(not i2_problems, f"I2 table validation failed: {i2_problems[:3]}")
    res_diff = _asset_object_diff(
        res_orig,
        res_final,
        allowed_textassets={TextAssetXmlFormat.ASSET_NAME},
        allowed_monobehaviours={i2_rep["path_id"]},
    )
    require(not res_diff, f"resources.assets diff touched unintended objects: {res_diff[:5]}")
    write_output(res_rel, res_final)
    report["formats"]["textasset_xml"] = {"units": len(xml_map)}
    report["formats"]["i2_mono"] = i2_rep

    # 5. Scene MonoBehaviours (level*, sharedassets*)
    scene_map: dict[str, str] = {}
    for row in rows:
        if row["format"] == "scene_mono":
            scene_map[row["EN"]] = by_id[row["id"]]
    scene_hits: dict[str, int] = {}
    scene_files_written = 0
    for rel in SceneMonoFormat.discover(en_root):
        orig_bytes = (en_root / rel).read_bytes()
        patched_bytes, sc_rep = SceneMonoFormat.patch_file_bytes(orig_bytes, scene_map)
        if not sc_rep["touched"]:
            continue
        for src_text, cnt in sc_rep["hits"].items():
            scene_hits[src_text] = scene_hits.get(src_text, 0) + cnt
        sc_problems = SceneMonoFormat.validate_bytes(
            patched_bytes, {k: scene_map[k] for k in sc_rep["hits"]}, filename=rel
        )
        require(not sc_problems, f"scene validation failed for {rel}: {sc_problems[:3]}")
        sc_diff = _asset_object_diff(orig_bytes, patched_bytes, allowed_monobehaviours=set(sc_rep["touched"]))
        require(not sc_diff, f"{rel} diff touched unintended objects: {sc_diff[:5]}")
        write_output(rel, patched_bytes)
        scene_files_written += 1
    missing_scene = sorted(set(scene_map) - set(scene_hits))
    require(not missing_scene, f"scene translations had no verified site: {missing_scene[:5]}")
    report["formats"]["scene_mono"] = {"written": scene_files_written, "units": len(scene_map)}

    # 6. Managed/Assembly-CSharp.dll
    dll_map: dict[str, dict[str, str]] = {}
    for row in rows:
        if row["format"] == "dll":
            tname = row["context"]["type"]
            dll_map.setdefault(tname, {})[row["EN"]] = by_id[row["id"]]
    if dll_map:
        dll_src = en_root / DllFormat.ASSEMBLY_REL
        usages = find_logic_usages(dll_src, dll_map)
        require(not usages, f"LOGIC_USAGE_DETECTED in Assembly-CSharp.dll: {usages[:5]}")
        dll_dst = staging_dir / DllFormat.ASSEMBLY_REL
        dll_rep = DllFormat.patch(dll_src, dll_dst, dll_map)
        dll_problems = DllFormat.validate_patched(dll_dst, dll_map)
        require(not dll_problems, f"dll validation failed: {dll_problems[:5]}")
        outputs[DllFormat.ASSEMBLY_REL] = sha256_bytes(dll_dst.read_bytes())
        report["formats"]["dll"] = {"total_sites": dll_rep.get("total", 0), "units": sum(len(v) for v in dll_map.values())}

    return outputs, report
