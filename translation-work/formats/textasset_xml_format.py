"""Unity TextAsset XML handler (LocalizationText inside resources.assets) via pure Python."""
from __future__ import annotations

from pathlib import Path
import re

from context import analyze_text_context
from .unity_assets import (
    CLASS_ID_TEXT_ASSET,
    encode_text_asset,
    load_asset,
    read_text_asset,
    rebuild_serialized_file,
)

ASSET_PATH = "The Iron Oath_Data/resources.assets"
ASSET_NAME = "LocalizationText"

_EN_SPAN_RE = re.compile(r"<EN>(.*?)</EN>", re.DOTALL)
_KEY_RE = re.compile(r'<TextKey\s+name="([^"]+)">')
_ENCLOSING_RE = re.compile(r'(<TextKey\s+name="[^"]+">.*?</TextKey>)', re.DOTALL)


class TextAssetXmlFormat:
    ASSET_PATH = ASSET_PATH
    ASSET_NAME = ASSET_NAME

    @classmethod
    def discover(cls, input_root: Path) -> list[str]:
        asset_path = input_root / ASSET_PATH
        if not asset_path.is_file():
            return []
        try:
            asset = load_asset(asset_path)
            for obj in asset.objects:
                if obj.class_id == CLASS_ID_TEXT_ASSET:
                    name, _script, _tail = read_text_asset(obj.raw)
                    if name == ASSET_NAME:
                        return [ASSET_PATH]
        except Exception:
            return []
        return []

    @classmethod
    def _load_xml(cls, asset_path: Path) -> tuple[int, str, bytes]:
        asset = load_asset(asset_path)
        for obj in asset.objects:
            if obj.class_id == CLASS_ID_TEXT_ASSET:
                name, script, tail = read_text_asset(obj.raw)
                if name == ASSET_NAME:
                    return obj.path_id, script, tail
        raise ValueError(f"TextAsset {ASSET_NAME!r} not found in {asset_path}")

    @classmethod
    def extract(cls, input_root: Path, relpath: str = ASSET_PATH) -> tuple[list[dict], dict]:
        _, xml, _ = cls._load_xml(input_root / relpath)
        records: list[dict] = []
        spans: dict[str, tuple[int, int]] = {}
        for block_match in _ENCLOSING_RE.finditer(xml):
            block = block_match.group(1)
            key_match = _KEY_RE.search(block)
            en_match = _EN_SPAN_RE.search(block)
            if key_match is None or en_match is None:
                continue
            key = key_match.group(1)
            raw_value = en_match.group(1)
            if not raw_value.strip():
                continue
            uid = f"textasset:{relpath}:{ASSET_NAME}:{key}"
            ctx = analyze_text_context(raw_value, kind="textasset_xml", table=ASSET_NAME, key=key)
            ctx["asset"] = ASSET_NAME
            records.append(
                {
                    "id": uid,
                    "row": len(records),
                    "source_file": relpath,
                    "format": "textasset_xml",
                    "key": key,
                    "context": ctx,
                    "EN": raw_value,
                    "PTBR": "",
                }
            )
            block_start = block_match.start(1)
            spans[uid] = (block_start + en_match.start(1), block_start + en_match.end(1))
        return records, spans

    @classmethod
    def rebuild_xml(cls, input_root: Path, relpath: str, translations_by_id: dict[str, str]) -> str:
        _, xml, _ = cls._load_xml(input_root / relpath)
        _, spans = cls.extract(input_root, relpath)
        edits: list[tuple[int, int, str]] = []
        for uid, (start, end) in spans.items():
            val = translations_by_id.get(uid)
            if val is not None:
                edits.append((start, end, val))
        for start, end, val in sorted(edits, reverse=True):
            xml = xml[:start] + val + xml[end:]
        xml = xml.replace('encoding="utf-16"', 'encoding="utf-8"', 1)
        return xml

    @classmethod
    def patch_asset_bytes(cls, asset_bytes: bytes, xml: str) -> bytes:
        from .unity_assets import parse_serialized_file

        asset = parse_serialized_file(asset_bytes)
        replacements: dict[int, bytes] = {}
        for obj in asset.objects:
            if obj.class_id == CLASS_ID_TEXT_ASSET:
                name, _script, tail = read_text_asset(obj.raw)
                if name == ASSET_NAME:
                    replacements[obj.path_id] = encode_text_asset(ASSET_NAME, xml, tail)
                    break
        if not replacements:
            raise ValueError(f"TextAsset {ASSET_NAME!r} not found")
        return rebuild_serialized_file(asset, replacements)

    @classmethod
    def validate_str(cls, xml: str, expected: dict[str, str]) -> int:
        mismatches = 0
        for block_match in _ENCLOSING_RE.finditer(xml):
            block = block_match.group(1)
            key_match = _KEY_RE.search(block)
            en_match = _EN_SPAN_RE.search(block)
            if key_match is None or en_match is None:
                continue
            key = key_match.group(1)
            value = en_match.group(1)
            wanted = None
            for expected_key, expected_value in expected.items():
                if expected_key == key or expected_key.endswith(":" + key):
                    wanted = expected_value
                    break
            if wanted is not None and value != wanted:
                mismatches += 1
        return mismatches
