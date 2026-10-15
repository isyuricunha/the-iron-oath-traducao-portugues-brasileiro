"""Scene/prefab UI text handler (level*.assets, sharedassets*.assets).

UI strings bake into serialized components that ship WITHOUT type trees.
Patching arbitrary length-prefixed strings would corrupt logic (sprite
names such as "Normal" are looked up by string at runtime), so this
handler only touches whitelisted (script pointer, field offset) sites
whose layouts were reverse engineered and verified against every
instance in the build:

    (1, 2702)  TextMeshProUGUI   m_Text at name_end + 40
    (1, 1070)  legacy UGUI Text  m_Text at name_end + 96
    (1, 637)   resist row        label at +84, description chained
                                 right after the padded label
    (1, 100)   text component    single string at +0
    (1, 166)   text component    single string at +0
    (1, 2543)  text component    single string at +0
    (1, 668)   text component    single string at +0
    (1, 201)   dropdown          options list count at +340, then
                                 per option: text string + image PPtr

Every other byte of every object is preserved. GameObject names,
component names, GUIDs, input action names, sprite names and
runtime-filled template texts are never touched.
"""

from __future__ import annotations

import os
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..models import TranslationUnit

BASE_DIRECTORY = "The Iron Oath_Data"
FILES = [
    "level0",
    "level1",
    "level2",
    "level3",
    "level4",
    "level5",
    "level6",
    "sharedassets0.assets",
    "sharedassets1.assets",
    "sharedassets2.assets",
    "sharedassets3.assets",
    "sharedassets4.assets",
    "sharedassets5.assets",
    "sharedassets6.assets",
]

_MONOBEHAVIOUR_HEADER = 0x1C
_PTR_SIZE = 12  # PPtr<Object> in serialized files: int32 fileID + int64 pathID


class SceneTextError(ValueError):
    pass


@dataclass(frozen=True)
class TextSite:
    """One (script, field) location that holds display text."""

    script: tuple[int, int]
    offset: int                    # bytes after the padded m_Name
    kind: str = "text"             # "text", "chained" or "options"


# Verified (script pointer, offset) display-text sites in this build.
TMP_TEXT_SCRIPT = (1, 2702)
TEXT_SITES: tuple[TextSite, ...] = (
    TextSite(TMP_TEXT_SCRIPT, 40),
    TextSite((1, 1070), 96),
    TextSite((1, 637), 84, "chained"),
    TextSite((1, 100), 0),
    TextSite((1, 166), 0),
    TextSite((1, 2543), 0),
    TextSite((1, 668), 0),
    TextSite((1, 201), 340, "options"),
)

_SITES_BY_SCRIPT: dict[tuple[int, int], list[TextSite]] = {}
for _site in TEXT_SITES:
    _SITES_BY_SCRIPT.setdefault(_site.script, []).append(_site)


@dataclass
class TextSpan:
    """One display-text string inside its raw object data."""

    path_id: int
    script: tuple[int, int]
    prefix_offset: int
    content_start: int
    content_end: int
    text: str


def _read_string(raw: bytes, offset: int) -> tuple[int, int, int, str] | None:
    """Return (prefix_offset, content_start, content_end, text) or None."""
    if offset < 0 or offset + 4 > len(raw):
        return None
    length = struct.unpack_from("<i", raw, offset)[0]
    if length < 0 or offset + 4 + length > len(raw) or length > 20000:
        return None
    content_start = offset + 4
    content_end = content_start + length
    try:
        text = raw[content_start:content_end].decode("utf-8")
    except UnicodeDecodeError:
        return None
    return offset, content_start, content_end, text


def _name_base(raw: bytes) -> int:
    name_len = struct.unpack_from("<i", raw, _MONOBEHAVIOUR_HEADER)[0]
    if name_len < 0 or _MONOBEHAVIOUR_HEADER + 4 + name_len > len(raw):
        raise SceneTextError("corrupt MonoBehaviour header")
    return (_MONOBEHAVIOUR_HEADER + 4 + name_len + 3) & ~3


def _option_spans(
    raw: bytes, list_offset: int, path_id: int, script: tuple[int, int]
) -> list[TextSpan]:
    """Walk a serialized List<OptionData>: per option, text then image PPtr."""
    spans: list[TextSpan] = []
    if list_offset + 4 > len(raw):
        return spans
    count = struct.unpack_from("<i", raw, list_offset)[0]
    if not (0 <= count <= 64):
        return spans
    cursor = list_offset + 4
    for _ in range(count):
        got = _read_string(raw, cursor)
        if got is None:
            break
        prefix_offset, content_start, content_end, text = got
        spans.append(TextSpan(path_id, script, prefix_offset, content_start, content_end, text))
        cursor = (content_end + 3) & ~3
        cursor += _PTR_SIZE
    return spans


def _spans_of_object(raw: bytes, path_id: int) -> list[TextSpan]:
    script = struct.unpack_from("<iq", raw, 0x10)
    sites = _SITES_BY_SCRIPT.get(script)
    if not sites:
        return []
    base = _name_base(raw)
    spans: list[TextSpan] = []
    for site in sites:
        if site.kind == "options":
            spans.extend(_option_spans(raw, base + site.offset, path_id, script))
            continue
        got = _read_string(raw, base + site.offset)
        if got is None:
            continue
        prefix_offset, content_start, content_end, text = got
        spans.append(TextSpan(path_id, script, prefix_offset, content_start, content_end, text))
        if site.kind == "chained":
            # Fields follow each other directly: the next string starts
            # right after this one's 4-byte alignment padding.
            chained = _read_string(raw, (content_end + 3) & ~3)
            if chained is not None:
                c_prefix, c_start, c_end, c_text = chained
                spans.append(TextSpan(path_id, script, c_prefix, c_start, c_end, c_text))
    return spans


def scan_file(path: Path) -> list[TextSpan]:
    """Return every whitelisted display-text span in one .assets file."""
    import UnityPy

    spans: list[TextSpan] = []
    env = UnityPy.load(str(path))
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        try:
            raw = obj.get_raw_data()
        except Exception:  # noqa: BLE001, S112 - foreign or corrupt objects are skipped
            continue
        if len(raw) < _MONOBEHAVIOUR_HEADER + 8:
            continue
        spans.extend(_spans_of_object(raw, obj.path_id))
    return spans


def apply_edits(
    raw: bytes, edits: list[tuple[int, int, str]], translations: dict[str, str]
) -> bytes:
    """Splice translated strings into raw object data, last span first.

    Each edit is (length-prefix offset, aligned end offset, source text);
    the replacement is a fresh 4-byte length prefix plus the new content
    padded back to 4-byte alignment.
    """
    for prefix_offset, edit_end, text in sorted(edits, reverse=True):
        content = translations[text].encode("utf-8")
        replacement = (
            struct.pack("<i", len(content))
            + content
            + b"\x00" * ((-len(content)) % 4)
        )
        raw = raw[:prefix_offset] + replacement + raw[edit_end:]
    return raw


class SceneMonoFormat:
    BASE_DIRECTORY = BASE_DIRECTORY
    FILES = FILES

    @classmethod
    def discover(cls, input_root: Path) -> list[str]:
        found = []
        for name in FILES:
            if (input_root / BASE_DIRECTORY / name).is_file():
                found.append(f"{BASE_DIRECTORY}/{name}")
        return found

    @classmethod
    def extract(
        cls, input_root: Path, relpath: str, translatable: set[str] | None = None
    ) -> tuple[list[TranslationUnit], dict]:
        """Extract units for whitelisted display-text values.

        With `translatable`, only values in the set are returned (map
        mode). Without it every non-empty value is extracted (census).
        Unit ids key on the English string so one translation covers
        every occurrence across all files.
        """
        rel = relpath if relpath.startswith(BASE_DIRECTORY) else f"{BASE_DIRECTORY}/{relpath}"
        spans = scan_file(input_root / rel)
        units: list[TranslationUnit] = []
        seen: set[str] = set()
        for span in spans:
            if not span.text.strip():
                continue
            if translatable is not None and span.text not in translatable:
                continue
            if span.text in seen:
                continue
            seen.add(span.text)
            units.append(
                TranslationUnit(
                    unit_id=f"scene:{span.text}",
                    source=span.text,
                    source_hash=f"{len(span.text)}:{span.text[:24]}",
                    path=rel,
                    context={"site": f"{span.script[1]}@{span.prefix_offset}"},
                )
            )
        return units, {}

    @classmethod
    def patch_file(cls, path: Path, translations: dict[str, str]) -> dict:
        """Replace whitelisted display text in place; report hits."""
        import UnityPy

        env = UnityPy.load(str(path))
        hits: dict[str, int] = {}
        touched: list[int] = []
        for obj in env.objects:
            if obj.type.name != "MonoBehaviour":
                continue
            try:
                raw = obj.get_raw_data()
            except Exception:  # noqa: BLE001, S112 - foreign or corrupt objects are skipped
                continue
            if len(raw) < _MONOBEHAVIOUR_HEADER + 8:
                continue
            spans = _spans_of_object(raw, obj.path_id)
            edits = [
                (
                    span.prefix_offset,
                    (span.content_end + 3) & ~3,
                    span.text,
                )
                for span in spans
                if span.text in translations
            ]
            if not edits:
                continue
            raw = apply_edits(raw, edits, translations)
            obj.set_raw_data(raw)
            touched.append(obj.path_id)
            for _prefix, _end, text in edits:
                hits[text] = hits.get(text, 0) + 1

        parent = path.parent
        if not touched:
            return {"patched_strings": 0, "touched": [], "hits": hits}
        tmp_dir = tempfile.mkdtemp(prefix=".scsave-", dir=str(parent))
        try:
            env.save(pack="none", out_path=tmp_dir)
            produced = Path(tmp_dir) / path.name
            if not produced.is_file():
                raise SceneTextError(f"UnityPy save did not produce {path.name}")
            os.replace(produced, path)
        finally:
            import shutil

            shutil.rmtree(tmp_dir, ignore_errors=True)
        return {"patched_strings": len(hits), "touched": touched, "hits": hits}

    @classmethod
    def validate_file(cls, path: Path, translations: dict[str, str]) -> list[str]:
        """Re-scan the saved file: targets present, sources gone."""
        problems: list[str] = []
        current = {span.text for span in scan_file(path)}
        for source, target in translations.items():
            if target not in current:
                problems.append(f"{path.name}: {target!r} not found after save")
            if source in current and source != target:
                problems.append(f"{path.name}: {source!r} still present after save")
        return problems
