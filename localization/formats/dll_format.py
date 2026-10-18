"""Assembly-CSharp.dll string patch handler (player-visible code strings).

Some interface text lives as ldstr literals in game code. Replacing every
string would corrupt logs and logic, so patches are scoped by declaring
type: the map lists {type: {english: portuguese}} and only those types'
literals are rewritten.

The heavy lifting runs in a small .NET console tool (Mono.Cecil):
localization/dotnet/DllStringPatch. This handler shells out to it. The
tool refuses to write when any mapped key has no ldstr site, and it
verifies the written assembly by re-reading it before returning.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from ..models import TranslationUnit

MANAGED_DIRECTORY = "The Iron Oath_Data/Managed"
ASSEMBLY_NAME = "Assembly-CSharp.dll"
DOTNET_TOOL_DIR = Path(__file__).resolve().parent.parent / "dotnet" / "DllStringPatch"


class DllPatchError(ValueError):
    pass


def _tool_binary() -> str:
    built = DOTNET_TOOL_DIR / "bin" / "Release" / "net8.0" / "DllStringPatch.dll"
    if built.is_file():
        return str(built)
    raise DllPatchError(
        "DllStringPatch is not built; run: dotnet build -c Release "
        f"{DOTNET_TOOL_DIR}"
    )


def _run_tool(args: list[str]) -> str:
    proc = subprocess.run(
        ["dotnet", _tool_binary(), *args],
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


def find_logic_usages(dll_path: Path, map_path: Path) -> list[list[str]]:
    """Report mapped strings the game compares at runtime.

    Runs the DllStringPatch `usages` mode: dataflow scan of every mapped
    (scope, en) ldstr operand for string comparisons, string-keyed
    collections and enum parses. Translating any reported entry breaks
    that comparison, so callers must refuse to publish while findings
    exist. Returns [scope, en, methodName] triples.
    """
    with tempfile.TemporaryDirectory(prefix="dllusages-") as tmp:
        out = Path(tmp) / "usages.json"
        _run_tool(["usages", str(dll_path), str(map_path), str(out)])
        return json.loads(out.read_text(encoding="utf-8"))


class DllFormat:
    ASSEMBLY_NAME = ASSEMBLY_NAME
    MANAGED_DIRECTORY = MANAGED_DIRECTORY

    @classmethod
    def discover(cls, input_root: Path) -> list[str]:
        if (input_root / MANAGED_DIRECTORY / ASSEMBLY_NAME).is_file():
            return [f"{MANAGED_DIRECTORY}/{ASSEMBLY_NAME}"]
        return []

    @classmethod
    def extract(
        cls, input_root: Path, relpath: str, scoped_map: dict[str, dict[str, str]] | None = None
    ) -> tuple[list[TranslationUnit], dict]:
        """Units for the mapped strings found in the assembly.

        The assembly is scanned once (`list` mode) and the result is
        filtered by the scoped translation map, so units exist only for
        strings this pass intends to patch.
        """
        dll_path = input_root / relpath
        with tempfile.TemporaryDirectory(prefix="dllscan-") as tmp:
            listing = Path(tmp) / "strings_by_type.json"
            _run_tool(["list", str(dll_path), str(listing)])
            by_type: dict[str, dict[str, int]] = json.loads(listing.read_text(encoding="utf-8"))

        units: list[TranslationUnit] = []
        sites: dict[str, int] = {}
        if scoped_map is None:
            scoped_map = {}
        for type_name, string_map in scoped_map.items():
            found = by_type.get(type_name)
            if found is None:
                continue
            for source in string_map:
                count = found.get(source, 0)
                if count:
                    units.append(
                        TranslationUnit(
                            unit_id=f"dll:{type_name}:{source}",
                            source=source,
                            source_hash=f"{len(source)}:{source[:24]}",
                            path=relpath,
                            context={"type": type_name, "ldstr_sites": count},
                        )
                    )
                    sites[f"{type_name}:{source}"] = count
        return units, sites

    @classmethod
    def patch(
        cls,
        input_root: Path,
        output_path: Path,
        scoped_map: dict[str, dict[str, str]],
    ) -> dict:
        """Write a patched copy of Assembly-CSharp.dll to output_path."""
        dll_path = input_root / MANAGED_DIRECTORY / ASSEMBLY_NAME
        output_path.parent.mkdir(parents=True, exist_ok=True)
        stdout = _run_tool(["patch", str(dll_path), _write_map(scoped_map), str(output_path)])
        return json.loads(stdout)

    @classmethod
    def validate_patched(cls, patched_path: Path, scoped_map: dict[str, dict[str, str]]) -> list[str]:
        """Re-scan the patched assembly; EN keys must be gone inside scope."""
        problems: list[str] = []
        with tempfile.TemporaryDirectory(prefix="dllverify-") as tmp:
            listing = Path(tmp) / "strings_by_type.json"
            _run_tool(["list", str(patched_path), str(listing)])
            by_type: dict[str, dict[str, int]] = json.loads(listing.read_text(encoding="utf-8"))
        for type_name, string_map in scoped_map.items():
            found = by_type.get(type_name, {})
            for source, target in string_map.items():
                if source in found:
                    problems.append(f"{type_name}: {source!r} still present after patch")
                if target not in found:
                    problems.append(f"{type_name}: {target!r} missing after patch")
        return problems


def _write_map(scoped_map: dict[str, dict[str, str]]) -> str:
    handle, path = tempfile.mkstemp(prefix="dllmap-", suffix=".json")
    with os.fdopen(handle, "w", encoding="utf-8") as fh:
        json.dump({"types": scoped_map}, fh, ensure_ascii=False, indent=1)
    return path


def ensure_tool_built() -> bool:
    """Build the .NET tool when its binary is missing."""
    try:
        _tool_binary()
        return True
    except DllPatchError:
        proc = subprocess.run(
            ["dotnet", "build", "-c", "Release", str(DOTNET_TOOL_DIR)],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        return proc.returncode == 0
