"""Portable Assembly-CSharp.dll scoped ldstr patcher via .NET 8 + Mono.Cecil."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from context import analyze_text_context

MANAGED_DIRECTORY = "The Iron Oath_Data/Managed"
ASSEMBLY_NAME = "Assembly-CSharp.dll"
ASSEMBLY_REL = f"{MANAGED_DIRECTORY}/{ASSEMBLY_NAME}"

_WORK_DIR = Path(__file__).resolve().parent.parent
VENDOR_TOOL_DIR = _WORK_DIR / "vendor" / "DllStringPatch"
VENDOR_TOOL_DLL = VENDOR_TOOL_DIR / "DllStringPatch.dll"


class DllPatchError(ValueError):
    pass


def check_dotnet_runtime() -> tuple[bool, str]:
    """Return (available, details) for .NET 8+ runtime."""
    dotnet = shutil.which("dotnet")
    if not dotnet:
        return False, "MISSING_RUNTIME: 'dotnet' executable not found on PATH (install .NET 8 Runtime / dotnet-runtime-8.0)"
    try:
        proc = subprocess.run([dotnet, "--list-runtimes"], capture_output=True, text=True, timeout=15, check=False)
    except OSError as exc:
        return False, f"MISSING_RUNTIME: failed invoking dotnet: {exc}"
    if proc.returncode != 0:
        return False, f"MISSING_RUNTIME: dotnet --list-runtimes exited {proc.returncode}: {proc.stderr.strip()}"
    lines = [l.strip() for l in proc.stdout.splitlines() if l.strip()]
    has_net8 = any("Microsoft.NETCore.App 8." in l or "Microsoft.NETCore.App 9." in l or "Microsoft.NETCore.App 10." in l for l in lines)
    if not has_net8:
        return False, f"MISSING_RUNTIME: Microsoft.NETCore.App >= 8.0 not found; got: {lines[:5]}"
    return True, "; ".join(l for l in lines if "Microsoft.NETCore.App" in l)


def _tool_binary() -> Path:
    for dep in (
        VENDOR_TOOL_DLL,
        VENDOR_TOOL_DIR / "DllStringPatch.deps.json",
        VENDOR_TOOL_DIR / "DllStringPatch.runtimeconfig.json",
        VENDOR_TOOL_DIR / "Mono.Cecil.dll",
    ):
        if not dep.is_file():
            raise DllPatchError(f"MISSING_DEPENDENCY: missing bundled runtime file {dep}")
    return VENDOR_TOOL_DLL


def _run_tool(args: list[str]) -> str:
    ok, detail = check_dotnet_runtime()
    if not ok:
        raise DllPatchError(detail)
    tool_dll = _tool_binary()
    proc = subprocess.run(
        ["dotnet", str(tool_dll), *args],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if proc.returncode != 0:
        raise DllPatchError(
            f"DllStringPatch failed ({proc.returncode}):\n{proc.stdout}\n{proc.stderr}"
        )
    return proc.stdout


def find_logic_usages(dll_path: Path, dll_map: dict[str, dict[str, str]]) -> list[list[str]]:
    """Verify no mapped DLL string is compared by game logic at runtime."""
    if not dll_map:
        return []
    with tempfile.TemporaryDirectory(prefix="dllusages-") as tmp:
        mp = Path(tmp) / "map.json"
        out = Path(tmp) / "usages.json"
        mp.write_text(json.dumps({"dll": dll_map}, ensure_ascii=False), encoding="utf-8")
        _run_tool(["usages", str(dll_path), str(mp), str(out)])
        return json.loads(out.read_text(encoding="utf-8"))


class DllFormat:
    ASSEMBLY_NAME = ASSEMBLY_NAME
    MANAGED_DIRECTORY = MANAGED_DIRECTORY
    ASSEMBLY_REL = ASSEMBLY_REL

    @classmethod
    def discover(cls, input_root: Path) -> list[str]:
        return [ASSEMBLY_REL] if (input_root / ASSEMBLY_REL).is_file() else []

    @classmethod
    def list_strings(cls, dll_path: Path) -> dict[str, dict[str, int]]:
        with tempfile.TemporaryDirectory(prefix="dllscan-") as tmp:
            listing = Path(tmp) / "strings_by_type.json"
            _run_tool(["list", str(dll_path), str(listing)])
            return json.loads(listing.read_text(encoding="utf-8"))

    @classmethod
    def extract(cls, input_root: Path, relpath: str = ASSEMBLY_REL, policy: dict | None = None) -> tuple[list[dict], dict]:
        dll_path = input_root / relpath
        by_type = cls.list_strings(dll_path)
        scope = (policy or {}).get("dll_scope", {})
        records: list[dict] = []
        sites: dict[str, int] = {}
        for type_name, source_list in scope.items():
            found = by_type.get(type_name)
            if found is None:
                continue
            for source in source_list:
                count = found.get(source, 0)
                if not count:
                    continue
                uid = f"dll:{type_name}:{source}"
                ctx = analyze_text_context(source, kind="dll_ldstr", table=type_name)
                ctx["type"] = type_name
                ctx["ldstr_sites"] = count
                records.append(
                    {
                        "id": uid,
                        "row": len(records),
                        "source_file": relpath,
                        "format": "dll",
                        "key": f"{type_name}::{source}",
                        "context": ctx,
                        "EN": source,
                        "PTBR": "",
                    }
                )
                sites[f"{type_name}:{source}"] = count
        return records, sites

    @classmethod
    def patch(cls, dll_path: Path, output_path: Path, scoped_map: dict[str, dict[str, str]]) -> dict:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="dllpatch-") as tmp:
            mp = Path(tmp) / "map.json"
            mp.write_text(json.dumps({"types": scoped_map}, ensure_ascii=False, indent=1), encoding="utf-8")
            stdout = _run_tool(["patch", str(dll_path), str(mp), str(output_path)])
            return json.loads(stdout)

    @classmethod
    def validate_patched(cls, patched_path: Path, scoped_map: dict[str, dict[str, str]]) -> list[str]:
        problems: list[str] = []
        by_type = cls.list_strings(patched_path)
        for type_name, string_map in scoped_map.items():
            found = by_type.get(type_name, {})
            for source, target in string_map.items():
                if source in found and source != target:
                    problems.append(f"{type_name}: {source!r} still present after patch")
                if target not in found:
                    problems.append(f"{type_name}: {target!r} missing after patch")
        return problems
