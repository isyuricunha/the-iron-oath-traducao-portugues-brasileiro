"""Unity TextAsset XML handler (the LocalizationText asset).

The asset holds an XML key table with per-language elements (<EN>, <DE>).
Only the <EN> values are translated; other languages stay untouched. The
edited XML is written back through UnityPy with the declaration adjusted
to utf-8, and the saved asset is re-parsed as validation.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..models import TranslationUnit

ASSET_PATH = "The Iron Oath_Data/resources.assets"
ASSET_NAME = "LocalizationText"

_EN_SPAN_RE = re.compile(r"<EN>(.*?)</EN>", re.DOTALL)
_KEY_RE = re.compile(r'<TextKey\s+name="([^"]+)">')
_ENCLOSING_RE = re.compile(r"(<TextKey\s+name=\"[^\"]+\">.*?</TextKey>)", re.DOTALL)


class TextAssetXmlFormat:
    @classmethod
    def discover(cls, input_root: Path) -> list[str]:
        asset = input_root / ASSET_PATH
        if not asset.is_file():
            return []
        try:
            import UnityPy

            env = UnityPy.load(str(asset))
            for obj in env.objects:
                if obj.type.name == "TextAsset" and cls._name_of(obj) == ASSET_NAME:
                    return [ASSET_PATH]
        except Exception:  # noqa: BLE001 - discovery must not crash on corrupt assets
            return []
        return []

    @staticmethod
    def _name_of(obj) -> str:
        try:
            data = obj.read_typetree()
            return str(data.get("m_Name") or "")
        except Exception:  # noqa: BLE001 - probe fallback for foreign asset types
            return ""

    @classmethod
    def _load_xml(cls, input_root: Path) -> tuple[str, str]:
        import UnityPy

        asset = input_root / ASSET_PATH
        env = UnityPy.load(str(asset))
        for obj in env.objects:
            if obj.type.name == "TextAsset" and cls._name_of(obj) == ASSET_NAME:
                data = obj.read_typetree()
                return str(data.get("m_Script") or ""), obj.path_id
        raise ValueError(f"TextAsset {ASSET_NAME!r} not found in {ASSET_PATH}")

    @classmethod
    def extract(cls, input_root: Path, relpath: str) -> tuple[list[TranslationUnit], dict]:
        xml, _ = cls._load_xml(input_root)
        units: list[TranslationUnit] = []
        spans: dict[str, tuple[int, int]] = {}
        for block_match in _ENCLOSING_RE.finditer(xml):
            block = block_match.group(1)
            key_match = _KEY_RE.search(block)
            if key_match is None:
                continue
            key = key_match.group(1)
            en_match = _EN_SPAN_RE.search(block)
            if en_match is None:
                continue
            raw_value = en_match.group(1)
            if not raw_value.strip():
                continue
            unit_id = f"textasset:{relpath}:{ASSET_NAME}:{key}"
            units.append(
                TranslationUnit(
                    unit_id=unit_id,
                    source=raw_value,
                    source_hash=f"{len(raw_value)}:{raw_value[:24]}",
                    path=relpath,
                    context={"key": key, "asset": ASSET_NAME},
                )
            )
            spans[unit_id] = (en_match.start(1), en_match.end(1))
        return units, spans

    @classmethod
    def rebuild(
        cls, input_root: Path, relpath: str, translations_by_id: dict[str, str]
    ) -> str:
        """Return the new XML body with <EN> values translated."""
        xml, _ = cls._load_xml(input_root)
        _, spans = cls.extract(input_root, relpath)
        edits: list[tuple[int, int, str]] = []
        for unit_id, (start, end) in spans.items():
            value = translations_by_id.get(unit_id)
            if value is None:
                continue
            edits.append((start, end, value))
        for start, end, value in sorted(edits, reverse=True):
            xml = xml[:start] + value + xml[end:]
        # The stored bytes become utf-8; the declaration must agree.
        xml = xml.replace('encoding="utf-16"', 'encoding="utf-8"', 1)
        return xml

    @classmethod
    def write_asset(cls, input_root: Path, staging_path: Path, xml: str) -> None:
        """Patch the LocalizationText asset in a staging copy of the file."""
        import UnityPy

        env = UnityPy.load(str(input_root / ASSET_PATH))
        for obj in env.objects:
            if obj.type.name == "TextAsset" and cls._name_of(obj) == ASSET_NAME:
                data = obj.read()
                data.m_Script = xml
                data.save()
        env.save(destination=str(staging_path))

    @classmethod
    def validate_str(cls, xml: str, expected: dict[str, str]) -> int:
        """Parse the rebuilt XML and assert EN values match the expected map."""
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