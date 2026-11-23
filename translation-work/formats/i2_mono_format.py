"""Pure-Python Unity I2 Localization table handler (I2Languages MonoBehaviour)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
import struct

from context import analyze_text_context
from .unity_assets import (
    CLASS_ID_MONOBEHAVIOUR,
    load_asset,
    parse_serialized_file,
    rebuild_serialized_file,
)

ASSET_PATH = "The Iron Oath_Data/resources.assets"
ASSET_NAME = "I2Languages"

_REFERENCE_RE = re.compile(r"^\{\*[^*]+\*\}$")


class I2ParseError(ValueError):
    """Raised when raw I2Languages bytes do not match the verified layout."""


@dataclass
class StringSpan:
    text: str
    prefix_offset: int
    content_start: int
    content_end: int
    padded_end: int = 0

    def __post_init__(self) -> None:
        if not self.padded_end:
            self.padded_end = self.prefix_offset + ((4 + self.content_end - self.content_start + 3) & ~3)


@dataclass
class I2Term:
    term: str
    term_span: StringSpan
    description: str
    values: list[StringSpan] = field(default_factory=list)
    flags: bytes = b""
    touch: list[StringSpan] = field(default_factory=list)


@dataclass
class I2Table:
    term_count: int
    terms: list[I2Term]
    languages: list[tuple[str, str, int]]
    pre_terms_offset: int
    post_terms_offset: int
    gap_bytes: bytes
    languages_offset: int
    post_languages_offset: int
    trailing_bytes: bytes

    def term_map(self) -> dict[str, I2Term]:
        return {t.term: t for t in self.terms}


class _Reader:
    def __init__(self, data: bytes, pos: int = 0):
        self.data = data
        self.pos = pos

    def i32(self) -> int:
        if self.pos + 4 > len(self.data):
            raise I2ParseError(f"truncated int32 at {self.pos}")
        value = struct.unpack_from("<i", self.data, self.pos)[0]
        self.pos += 4
        return value

    def string(self) -> StringSpan:
        prefix_offset = self.pos
        length = self.i32()
        if length < 0 or self.pos + length > len(self.data):
            raise I2ParseError(f"bad string length {length} at {prefix_offset}")
        content_start = self.pos
        self.pos += length
        try:
            text = self.data[content_start:self.pos].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise I2ParseError(f"invalid utf-8 string at {prefix_offset}: {exc}") from None
        self.pos = (self.pos + 3) & ~3
        return StringSpan(text, prefix_offset, content_start, content_start + length)

    def byte_array(self) -> bytes:
        length = self.i32()
        if length < 0 or self.pos + length > len(self.data):
            raise I2ParseError(f"bad byte array length {length} at {self.pos - 4}")
        value = self.data[self.pos : self.pos + length]
        self.pos += length
        self.pos = (self.pos + 3) & ~3
        return value

    def string_array(self) -> list[StringSpan]:
        count = self.i32()
        if count < 0 or count > 1_000_000:
            raise I2ParseError(f"bad array count {count} at {self.pos - 4}")
        return [self.string() for _ in range(count)]


def _parse_header(data: bytes) -> int:
    if len(data) < 32:
        raise I2ParseError("MonoBehaviour object too short")
    reader = _Reader(data)
    _game_obj_file_id = reader.i32()
    reader.pos += 8  # m_GameObject pathID
    reader.pos = (reader.pos + 4) & ~3  # m_Enabled + align
    _script_file_id = reader.i32()
    reader.pos += 8  # m_Script pathID
    name = reader.string()
    if name.text != ASSET_NAME:
        raise I2ParseError(f"object is not {ASSET_NAME!r}: {name.text!r}")
    return reader.pos


def parse_table(raw: bytes) -> I2Table:
    name_end = _parse_header(raw)
    pre_terms_offset = name_end
    if pre_terms_offset + 12 > len(raw):
        raise I2ParseError("truncated I2 pre-terms region")
    region = struct.unpack_from("<3i", raw, pre_terms_offset)
    if region != (0, 0, 1):
        raise I2ParseError(f"unexpected pre-terms region {region} at {pre_terms_offset}")
    reader = _Reader(raw, pre_terms_offset + 12)
    term_count = reader.i32()
    if not (0 < term_count < 1_000_000):
        raise I2ParseError(f"implausible term count {term_count}")

    terms: list[I2Term] = []
    for _ in range(term_count):
        term_span = reader.string()
        description = reader.string()
        values = reader.string_array()
        if not values:
            raise I2ParseError(f"term {term_span.text!r} has no language columns")
        flags = reader.byte_array()
        touch = reader.string_array()
        terms.append(I2Term(term_span.text, term_span, description.text, values, flags, touch))

    post_terms_offset = reader.pos
    gap = _Reader(raw, post_terms_offset)
    gap_bytes = raw[post_terms_offset : post_terms_offset + 12]
    gap.pos += 12
    language_count = gap.i32()
    languages: list[tuple[str, str, int]] = []
    for _ in range(language_count):
        name = gap.string()
        code = gap.string()
        flags = gap.i32()
        languages.append((name.text, code.text, flags))
    gap.pos = (gap.pos + 4) & ~3  # CaseSensitiveTerms
    gap.pos = (gap.pos + 4) & ~3  # CaseSensitiveCategories
    _url, _key, _name, _version = gap.string(), gap.string(), gap.string(), gap.string()
    post_languages_offset = gap.pos
    trailing = raw[gap.pos:]

    return I2Table(
        term_count=term_count,
        terms=terms,
        languages=languages,
        pre_terms_offset=pre_terms_offset,
        post_terms_offset=post_terms_offset,
        gap_bytes=gap_bytes,
        languages_offset=post_terms_offset + 12,
        post_languages_offset=post_languages_offset,
        trailing_bytes=trailing,
    )


def is_translatable(text: str) -> bool:
    return bool(text.strip()) and not _REFERENCE_RE.match(text)


def apply_translations(raw: bytes, table: I2Table, translations: dict[str, str]) -> bytes:
    by_term = table.term_map()
    edits: list[tuple[int, int, str, bytes]] = []
    for term_key, target in translations.items():
        if term_key not in by_term:
            raise I2ParseError(f"unknown I2 term: {term_key}")
        span = by_term[term_key].values[0]
        content = target.encode("utf-8")
        new_bytes = struct.pack("<i", len(content)) + content + b"\x00" * ((-len(content)) % 4)
        edits.append((span.prefix_offset, span.padded_end, term_key, new_bytes))
    for prefix_offset, edit_end, _term_key, new_bytes in sorted(edits, reverse=True):
        raw = raw[:prefix_offset] + new_bytes + raw[edit_end:]
    return raw


class I2MonoFormat:
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
                if obj.class_id != CLASS_ID_MONOBEHAVIOUR:
                    continue
                try:
                    if parse_table(obj.raw) is not None:
                        return [ASSET_PATH]
                except I2ParseError:
                    continue
        except Exception:
            return []
        return []

    @staticmethod
    def find_object(asset):
        for obj in asset.objects:
            if obj.class_id != CLASS_ID_MONOBEHAVIOUR:
                continue
            try:
                _parse_header(obj.raw)
                return obj
            except I2ParseError:
                continue
        return None

    @classmethod
    def extract(cls, input_root: Path, relpath: str = ASSET_PATH, policy: dict | None = None) -> tuple[list[dict], dict]:
        asset = load_asset(input_root / relpath)
        obj = cls.find_object(asset)
        if obj is None:
            raise ValueError(f"{ASSET_NAME!r} not found in {relpath}")
        table = parse_table(obj.raw)
        skip_terms = set((policy or {}).get("i2_skip_terms", []))

        records: list[dict] = []
        spans: dict[str, tuple[int, int, int]] = {}
        for entry in table.terms:
            if entry.term in skip_terms:
                continue
            span = entry.values[0]
            if not is_translatable(span.text):
                continue
            uid = f"i2:{entry.term}"
            ctx = analyze_text_context(span.text, kind="i2_mono", table=ASSET_NAME, key=entry.term)
            ctx["table"] = ASSET_NAME
            records.append(
                {
                    "id": uid,
                    "row": len(records),
                    "source_file": relpath,
                    "format": "i2_mono",
                    "key": entry.term,
                    "context": ctx,
                    "EN": span.text,
                    "PTBR": "",
                }
            )
            spans[uid] = (span.prefix_offset, span.content_start, span.content_end)
        return records, spans

    @classmethod
    def patch_asset_bytes(cls, asset_bytes: bytes, translations: dict[str, str]) -> tuple[bytes, dict]:
        asset = parse_serialized_file(asset_bytes)
        obj = cls.find_object(asset)
        if obj is None:
            raise ValueError(f"{ASSET_NAME!r} not found")
        table = parse_table(obj.raw)
        by_term = table.term_map()
        changed = sum(1 for term, value in translations.items() if by_term[term].values[0].text != value)
        patched_raw = apply_translations(obj.raw, table, translations)
        rebuilt = rebuild_serialized_file(asset, {obj.path_id: patched_raw})
        return rebuilt, {
            "path_id": obj.path_id,
            "terms": table.term_count,
            "patched": len(translations),
            "changed": changed,
        }

    @classmethod
    def validate_bytes(cls, asset_bytes: bytes, translations: dict[str, str]) -> list[str]:
        asset = parse_serialized_file(asset_bytes)
        obj = cls.find_object(asset)
        if obj is None:
            return [f"{ASSET_NAME!r} vanished after rebuild"]
        table = parse_table(obj.raw)
        problems: list[str] = []
        by_term = table.term_map()
        for term_key, expected in translations.items():
            entry = by_term.get(term_key)
            if entry is None:
                problems.append(f"term vanished after save: {term_key}")
            elif entry.values[0].text != expected:
                problems.append(f"{term_key}: expected {expected!r}, found {entry.values[0].text!r}")
        return problems
