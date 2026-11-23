"""Read-only portability preflight for Windows and Ubuntu Server 24.04 LTS ARM64.

Verifies Python version, CPU architecture, layered integrity receipts
(runtime-support.json -> toolchain.json -> engine-shapes.json -> tool files),
committed EN source integrity, .NET 8 runtime + Mono.Cecil assembly resolution,
and synthetic lossless parse/rebuild across all native format engines.
Never translates text, installs packages, requires sudo/SSH, or writes to EN/PTBR.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import platform
import struct
import subprocess
import sys
import tempfile

from formats.codex_xml_format import CodexXmlFormat
from formats.dll_format import VENDOR_TOOL_DLL, check_dotnet_runtime
from formats.hjson_format import HjsonFormat
from formats.i2_mono_format import apply_translations as i2_apply, parse_table as i2_parse
from formats.scene_mono_format import SceneMonoFormat, TMP_TEXT_SCRIPT, apply_edits as scene_apply
scene_patch = SceneMonoFormat.patch_file_bytes
from formats.textasset_xml_format import TextAssetXmlFormat
from formats.unity_assets import (
    CLASS_ID_MONOBEHAVIOUR,
    CLASS_ID_TEXT_ASSET,
    build_minimal_assets,
    encode_text_asset,
    parse_serialized_file,
    read_text_asset,
)
from formats.yarn_format import YarnFormat
from native import Invalid, require


def normalized_bytes(path: Path, mode: str) -> bytes:
    raw = path.read_bytes()
    if mode == "git-lf":
        return raw.replace(b"\r\n", b"\n")
    if mode == "binary":
        return raw
    raise Invalid(f"MALFORMED_TEST_DATA: invalid integrity mode {mode!r} for {path}")


def _build_synthetic_i2_raw() -> bytes:
    def s(val: str) -> bytes:
        b = val.encode("utf-8")
        return struct.pack("<i", len(b)) + b + b"\x00" * ((-len(b)) % 4)

    header = (
        struct.pack("<iq", 0, 0)
        + b"\x01\x00\x00\x00"
        + struct.pack("<iq", 1, 532)
        + s("I2Languages")
        + struct.pack("<3i", 0, 0, 1)
    )
    terms = bytearray(header)
    terms += struct.pack("<i", 1)
    terms += s("tio_Smoke") + s("") + struct.pack("<i", 3) + s("Smoke Term") + s("") + s("")
    terms += struct.pack("<i", 3) + b"\x00\x00\x00\x00"
    terms += struct.pack("<i", 0)
    terms += b"\x00" * 12
    terms += struct.pack("<i", 1) + s("English") + s("en") + struct.pack("<i", 0)
    terms += b"\x01\x00\x00\x00\x00\x00\x00\x00" + s("") + s("") + s("") + s("0")
    return bytes(terms)


def _build_synthetic_scene_raw(text: str) -> bytes:
    def s(val: str) -> bytes:
        b = val.encode("utf-8")
        return struct.pack("<i", len(b)) + b + b"\x00" * ((-len(b)) % 4)

    return (
        struct.pack("<iq", 7, 100)
        + b"\x01\x00\x00\x00"
        + struct.pack("<iq", *TMP_TEXT_SCRIPT)
        + s("SmokeLabel")
        + b"\x00" * 40
        + s(text)
    )


def verify_layered_integrity(root: Path) -> dict:
    """Validate runtime-support.json -> toolchain.json -> engine-shapes.json + bundled files."""
    tw = root / "translation-work"
    runtime_path = tw / "runtime-support.json"
    require(runtime_path.is_file(), "MISSING_DEPENDENCY: missing translation-work/runtime-support.json")
    try:
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Invalid(f"MALFORMED_TEST_DATA: invalid runtime-support.json: {exc}") from exc

    require(
        runtime.get("schema") == 1 and isinstance(runtime.get("toolchain_sha256"), str),
        "MALFORMED_TEST_DATA: invalid runtime-support.json schema",
    )

    tc_path = tw / "toolchain.json"
    require(tc_path.is_file(), "MISSING_DEPENDENCY: missing translation-work/toolchain.json")
    tc_digest = hashlib.sha256(normalized_bytes(tc_path, "git-lf")).hexdigest()
    require(
        tc_digest == runtime["toolchain_sha256"],
        "CHECKSUM_MISMATCH: translation-work/toolchain.json differs from runtime-support.json",
    )
    try:
        tc = json.loads(tc_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Invalid(f"MALFORMED_TEST_DATA: invalid toolchain.json: {exc}") from exc
    require(tc.get("schema") == 1 and isinstance(tc.get("files"), dict), "MALFORMED_TEST_DATA: invalid toolchain.json schema")

    shapes_path = tw / "engine-shapes.json"
    require(shapes_path.is_file(), "MISSING_DEPENDENCY: missing translation-work/engine-shapes.json")
    require("translation-work/engine-shapes.json" in tc["files"], "MALFORMED_TEST_DATA: engine-shapes.json not linked in toolchain.json")
    try:
        shapes = json.loads(shapes_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Invalid(f"MALFORMED_TEST_DATA: invalid engine-shapes.json: {exc}") from exc
    require(shapes.get("schema") == 1 and shapes.get("game") == "The Iron Oath", "MALFORMED_TEST_DATA: invalid engine-shapes.json")

    for rel, spec in tc["files"].items():
        fpath = root / rel
        require(fpath.is_file(), f"MISSING_DEPENDENCY: missing toolkit file {rel}")
        actual = hashlib.sha256(normalized_bytes(fpath, spec["mode"])).hexdigest()
        require(
            actual == spec["sha256"],
            f"CHECKSUM_MISMATCH: {rel} differs from toolchain.json (expected {spec['sha256'][:12]}, got {actual[:12]})",
        )
    return runtime


def run_doctor(
    root: Path,
    *,
    smoke_dll_path: Path | None = None,
    check_en_sources: bool = True,
) -> dict:
    """Execute read-only portability and runtime verification checks."""
    root = Path(root).resolve()
    require(sys.version_info >= (3, 10), f"MISSING_RUNTIME: Python >= 3.10 required, found {platform.python_version()}")
    machine = platform.machine().lower()
    require(
        machine in ("amd64", "x86_64", "arm64", "aarch64"),
        f"UNSUPPORTED_ARCHITECTURE: unsupported machine {machine!r}",
    )

    runtime = verify_layered_integrity(root)

    # Verify committed EN source bytes match translation-work/translations/manifest.json
    manifest_path = root / "translation-work" / "translations" / "manifest.json"
    source_count = 0
    if check_en_sources and manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise Invalid(f"MALFORMED_TEST_DATA: invalid manifest.json: {exc}") from exc
        sources = manifest.get("sources", {})
        source_count = len(sources)
        for rel, expected_sha in sources.items():
            src_file = root / "EN" / rel
            require(src_file.is_file(), f"MISSING_DEPENDENCY: missing EN source {rel}")
            actual_sha = hashlib.sha256(src_file.read_bytes()).hexdigest()
            require(actual_sha == expected_sha, f"CHECKSUM_MISMATCH: original EN/{rel} modified outside sync")

    # Verify .NET 8+ runtime and live execution of bundled DllStringPatch + Mono.Cecil.dll
    ok_dotnet, dotnet_info = check_dotnet_runtime()
    require(ok_dotnet, dotnet_info)
    for dep in (
        VENDOR_TOOL_DLL,
        VENDOR_TOOL_DLL.with_name("DllStringPatch.deps.json"),
        VENDOR_TOOL_DLL.with_name("DllStringPatch.runtimeconfig.json"),
        VENDOR_TOOL_DLL.with_name("Mono.Cecil.dll"),
    ):
        require(dep.is_file(), f"MISSING_DEPENDENCY: missing {dep.name} in vendor/DllStringPatch")

    dll_fixture = smoke_dll_path or (root / "translation-work" / "tests" / "fixtures" / "smoke-sample.dll")
    require(dll_fixture.is_file(), f"MISSING_DEPENDENCY: missing smoke DLL fixture {dll_fixture}")

    with tempfile.TemporaryDirectory(prefix="tio-doctor-") as tmp:
        tmp_dir = Path(tmp)

        # 1. Synthetic .dll list + patch roundtrip proving Mono.Cecil assembly resolution on live runtime
        list_out = tmp_dir / "dll_list.json"
        p_list = subprocess.run(
            ["dotnet", str(VENDOR_TOOL_DLL), "list", str(dll_fixture), str(list_out)],
            capture_output=True, text=True, timeout=60, check=False,
        )
        require(
            p_list.returncode == 0,
            f"MISSING_DEPENDENCY: DllStringPatch failed loading/listing fixture ({p_list.returncode}): {p_list.stderr.strip()}",
        )
        try:
            listed = json.loads(list_out.read_text(encoding="utf-8"))
        except Exception as exc:
            raise Invalid(f"MALFORMED_TEST_DATA: bad DllStringPatch list output: {exc}") from exc
        require("SmokeFixture.SampleDialog" in listed, f"MALFORMED_TEST_DATA: unexpected fixture types {list(listed)}")

        patch_map = tmp_dir / "dll_map.json"
        patch_map.write_text(
            json.dumps({"types": {"SmokeFixture.SampleDialog": {"Fixture Label": "Rotulo de Teste"}}}),
            encoding="utf-8",
        )
        patched_dll = tmp_dir / "patched.dll"
        p_patch = subprocess.run(
            ["dotnet", str(VENDOR_TOOL_DLL), "patch", str(dll_fixture), str(patch_map), str(patched_dll)],
            capture_output=True, text=True, timeout=60, check=False,
        )
        require(
            p_patch.returncode == 0 and patched_dll.is_file(),
            f"MALFORMED_TEST_DATA: DllStringPatch patch smoke failed: {p_patch.stderr.strip()}",
        )

        # 2. Synthetic HJSON lossless roundtrip
        hjson_dir = tmp_dir / "The Iron Oath_Data" / "StreamingAssets" / "Data" / "Items"
        hjson_dir.mkdir(parents=True)
        hjson_sample = b'{\r\n\tUniqueId: "smoke_1",\r\n\tName: "Iron Sword",\r\n}\r\n'
        (hjson_dir / "smoke.json").write_bytes(hjson_sample)
        h_recs, _ = HjsonFormat.extract(tmp_dir, "The Iron Oath_Data/StreamingAssets/Data/Items/smoke.json")
        require(len(h_recs) == 1, "MALFORMED_TEST_DATA: HJSON smoke extract failed")
        require(
            HjsonFormat.rebuild(tmp_dir, "The Iron Oath_Data/StreamingAssets/Data/Items/smoke.json", {}) == hjson_sample,
            "MALFORMED_TEST_DATA: HJSON smoke rebuild failed",
        )

        # 3. Synthetic Yarn lossless roundtrip
        yarn_dir = tmp_dir / "The Iron Oath_Data" / "StreamingAssets" / "Data" / "QuestData"
        yarn_dir.mkdir(parents=True)
        yarn_sample = b'title: SE.Smoke\r\n---\r\n->[Action:Continue]\r\n===\r\n'
        (yarn_dir / "Smoke.yarn.txt").write_bytes(yarn_sample)
        y_recs, _ = YarnFormat.extract(tmp_dir, "The Iron Oath_Data/StreamingAssets/Data/QuestData/Smoke.yarn.txt")
        require(len(y_recs) == 1 and y_recs[0]["EN"] == "Continue", "MALFORMED_TEST_DATA: Yarn smoke extract failed")
        require(
            YarnFormat.rebuild(tmp_dir, "The Iron Oath_Data/StreamingAssets/Data/QuestData/Smoke.yarn.txt", {}) == yarn_sample,
            "MALFORMED_TEST_DATA: Yarn smoke rebuild failed",
        )

        # 4. Synthetic Codex XML lossless roundtrip
        codex_dir = tmp_dir / "The Iron Oath_Data" / "StreamingAssets" / "Data" / "Codex"
        codex_dir.mkdir(parents=True)
        codex_sample = b'<CodexDoc><codexCategory name="Smoke" id="smoke"><description>Smoke lore</description></codexCategory></CodexDoc>'
        (codex_dir / "codex.xml").write_bytes(codex_sample)
        c_recs, _ = CodexXmlFormat.extract(tmp_dir, "The Iron Oath_Data/StreamingAssets/Data/Codex/codex.xml")
        require(len(c_recs) == 2, "MALFORMED_TEST_DATA: Codex XML smoke extract failed")
        require(
            CodexXmlFormat.rebuild(tmp_dir, "The Iron Oath_Data/StreamingAssets/Data/Codex/codex.xml", {}) == codex_sample,
            "MALFORMED_TEST_DATA: Codex XML smoke rebuild failed",
        )

        # 5. Synthetic Unity v21 .assets + TextAsset + I2Languages + Scene MonoBehaviour roundtrip
        xml_body = '<?xml version="1.0" encoding="utf-8"?><Localization><TextKey name="k"><EN>Hello</EN></TextKey></Localization>'
        ta_raw = encode_text_asset("LocalizationText", xml_body)
        i2_raw = _build_synthetic_i2_raw()
        sc_raw = _build_synthetic_scene_raw("Smoke Button")
        asset_bytes = build_minimal_assets(
            [
                (96, CLASS_ID_TEXT_ASSET, ta_raw),
                (1841, CLASS_ID_MONOBEHAVIOUR, i2_raw),
                (2000, CLASS_ID_MONOBEHAVIOUR, sc_raw),
            ]
        )
        parsed = parse_serialized_file(asset_bytes)
        require(len(parsed.objects) == 3, "MALFORMED_TEST_DATA: synthetic SerializedFile parse failed")
        t_name, t_script, _ = read_text_asset(parsed.objects[0].raw)
        require(t_name == "LocalizationText" and "Hello" in t_script, "MALFORMED_TEST_DATA: TextAsset parse failed")
        table = i2_parse(parsed.objects[1].raw)
        require(i2_apply(parsed.objects[1].raw, table, {"tio_Smoke": "Smoke Term"}) == i2_raw, "MALFORMED_TEST_DATA: I2 smoke failed")
        sc_patched, sc_rep = scene_patch(asset_bytes, {"Smoke Button": "Smoke Button"})
        require(sc_rep["patched_strings"] == 1 and len(parse_serialized_file(sc_patched).objects) == 3, "MALFORMED_TEST_DATA: Scene smoke failed")

    arm64_verified = platform.system() == "Linux" and machine in ("arm64", "aarch64")
    return {
        "status": "ok",
        "runtime_python": platform.python_version(),
        "runtime_dotnet": dotnet_info,
        "architecture": machine,
        "system": platform.system(),
        "expected_handoff_platform": runtime.get("expected_handoff_platform", "Ubuntu Server 24.04 LTS ARM64/aarch64"),
        "arm64_execution": "verified by this invocation" if arm64_verified else "pending first Ubuntu ARM64 preflight",
        "sources": source_count,
        "read_only": True,
    }
