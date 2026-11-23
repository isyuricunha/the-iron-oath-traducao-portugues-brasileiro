"""Pure-Python Unity scene/prefab display-text handler (level*, sharedassets*)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import struct

from context import analyze_text_context
from .unity_assets import (
    CLASS_ID_MONOBEHAVIOUR,
    load_asset,
    parse_serialized_file,
    rebuild_serialized_file,
)

BASE_DIRECTORY = "The Iron Oath_Data"
FILES = [
    "level0", "level1", "level2", "level3", "level4", "level5", "level6",
    "sharedassets0.assets", "sharedassets1.assets", "sharedassets2.assets",
    "sharedassets3.assets", "sharedassets4.assets", "sharedassets5.assets", "sharedassets6.assets",
]

_MONOBEHAVIOUR_HEADER = 0x1C
_PTR_SIZE = 12


class SceneTextError(ValueError):
    pass


@dataclass(frozen=True)
class TextSite:
    script: tuple[int, int]
    offset: int
    kind: str = "text"


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
    path_id: int
    script: tuple[int, int]
    prefix_offset: int
    content_start: int
    content_end: int
    text: str


def _read_string(raw: bytes, offset: int) -> tuple[int, int, int, str] | None:
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


def _option_spans(raw: bytes, list_offset: int, path_id: int, script: tuple[int, int]) -> list[TextSpan]:
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
    if len(raw) < _MONOBEHAVIOUR_HEADER + 8:
        return []
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
            chained = _read_string(raw, (content_end + 3) & ~3)
            if chained is not None:
                c_prefix, c_start, c_end, c_text = chained
                spans.append(TextSpan(path_id, script, c_prefix, c_start, c_end, c_text))
    return spans


def scan_asset(asset) -> list[TextSpan]:
    spans: list[TextSpan] = []
    for obj in asset.objects:
        if obj.class_id == CLASS_ID_MONOBEHAVIOUR:
            spans.extend(_spans_of_object(obj.raw, obj.path_id))
    return spans


def apply_edits(raw: bytes, edits: list[tuple[int, int, str]], translations: dict[str, str]) -> bytes:
    for prefix_offset, edit_end, text in sorted(edits, reverse=True):
        content = translations[text].encode("utf-8")
        replacement = struct.pack("<i", len(content)) + content + b"\x00" * ((-len(content)) % 4)
        raw = raw[:prefix_offset] + replacement + raw[edit_end:]
    return raw


class SceneMonoFormat:
    BASE_DIRECTORY = BASE_DIRECTORY
    FILES = FILES

    @classmethod
    def discover(cls, input_root: Path) -> list[str]:
        return [
            f"{BASE_DIRECTORY}/{name}"
            for name in FILES
            if (input_root / BASE_DIRECTORY / name).is_file()
        ]

    @classmethod
    def extract_all(cls, input_root: Path, relpaths: list[str], policy: dict | None = None) -> list[dict]:
        """Extract deduplicated scene UI strings respecting source-policy scene_ignore."""
        ignored = set((policy or {}).get("scene_ignore", []))
        by_text: dict[str, dict] = {}
        for rel in relpaths:
            asset = load_asset(input_root / rel)
            for span in scan_asset(asset):
                if not span.text.strip() or span.text in ignored:
                    continue
                if span.text not in by_text:
                    ctx = analyze_text_context(span.text, kind="scene_ui", table=rel)
                    ctx["sites"] = [f"{rel}:{span.path_id}@{span.prefix_offset}"]
                    by_text[span.text] = {
                        "id": f"scene:{span.text}",
                        "row": len(by_text),
                        "source_file": rel,
                        "format": "scene_mono",
                        "key": f"{span.script[1]}@{span.prefix_offset}",
                        "context": ctx,
                        "EN": span.text,
                        "PTBR": "",
                    }
                else:
                    sites = by_text[span.text]["context"]["sites"]
                    site_tag = f"{rel}:{span.path_id}@{span.prefix_offset}"
                    if len(sites) < 10 and site_tag not in sites:
                        sites.append(site_tag)
        return list(by_text.values())

    @classmethod
    def patch_file_bytes(cls, asset_bytes: bytes, translations: dict[str, str]) -> tuple[bytes, dict]:
        asset = parse_serialized_file(asset_bytes)
        replacements: dict[int, bytes] = {}
        hits: dict[str, int] = {}
        touched: list[int] = []
        for obj in asset.objects:
            if obj.class_id != CLASS_ID_MONOBEHAVIOUR:
                continue
            spans = _spans_of_object(obj.raw, obj.path_id)
            edits = [
                (sp.prefix_offset, (sp.content_end + 3) & ~3, sp.text)
                for sp in spans
                if sp.text in translations
            ]
            if not edits:
                continue
            replacements[obj.path_id] = apply_edits(obj.raw, edits, translations)
            touched.append(obj.path_id)
            for _p, _e, text in edits:
                hits[text] = hits.get(text, 0) + 1
        if not replacements:
            return asset_bytes, {"patched_strings": 0, "touched": [], "hits": hits}
        rebuilt = rebuild_serialized_file(asset, replacements)
        return rebuilt, {"patched_strings": len(hits), "touched": touched, "hits": hits}

    @classmethod
    def validate_bytes(cls, asset_bytes: bytes, translations: dict[str, str], filename: str = "") -> list[str]:
        asset = parse_serialized_file(asset_bytes)
        current = {sp.text for sp in scan_asset(asset)}
        problems: list[str] = []
        for source, target in translations.items():
            if target not in current:
                problems.append(f"{filename}: {target!r} not found after save")
            if source in current and source != target:
                problems.append(f"{filename}: {source!r} still present after save")
        return problems
