"""Unity I2 Localization table handler (the I2Languages MonoBehaviour).

This build ships the I2Languages MonoBehaviour WITHOUT type trees, so the
table is parsed structurally from the raw object data. The layout below
was reverse engineered and is asserted while parsing; any mismatch fails
loudly instead of producing a corrupt asset.

    MonoBehaviour header: m_GameObject PPtr, m_Enabled, m_Script PPtr, m_Name
    opaque pre-region (bytes preserved verbatim)
    int32                term count
    per term:
        string           Term
        string           Description
        string[]         Languages   (columns: English, Spanish, Description)
        byte[]           Flags
        string[]         Languages_Touch
    opaque gap (bytes preserved verbatim)
    int32                language count
    per language:        string name, string code, int32 flags
    bool                 CaseSensitiveTerms
    bool                 CaseSensitiveCategories
    string               Google_WebServiceURL
    string               Google_SpreadsheetKey
    string               Google_SpreadsheetName
    string               Google_LastUpdatedVersion
    opaque tail (bytes preserved verbatim)

Only the first language column (English) of each term is translated;
every other byte of the object is preserved verbatim. Pure I2 cross
references ({*tio_key*}) and empty values are never extracted. The patch
rewrites the raw object data in place and the saved file is re-parsed as
validation.
"""

from __future__ import annotations

import os
import re
import struct
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from ..models import TranslationUnit

ASSET_PATH = "The Iron Oath_Data/resources.assets"
ASSET_NAME = "I2Languages"

# Pure I2 cross references resolve to other terms at runtime and must be
# copied verbatim, never translated.
_REFERENCE_RE = re.compile(r"^\{\*[^*]+\*\}$")


class I2ParseError(ValueError):
    """The raw object data does not match the expected table layout."""


@dataclass
class StringSpan:
    """One serialized string inside the raw block."""

    text: str
    prefix_offset: int
    content_start: int
    content_end: int
    padded_end: int = 0

    def __post_init__(self) -> None:
        if not self.padded_end:
            # Unity aligns each string to 4 bytes: len prefix + content
            # + padding. Rebuilds must consume the padding too.
            self.padded_end = self.prefix_offset + ((4 + self.content_end - self.content_start + 3) & ~3)


@dataclass
class I2Term:
    """One TermData entry with byte spans for its language columns."""

    term: str
    term_span: StringSpan
    description: str
    values: list[StringSpan] = field(default_factory=list)
    flags: bytes = b""
    touch: list[StringSpan] = field(default_factory=list)


@dataclass
class I2Table:
    """Parsed I2Languages table with byte spans for surgical patching."""

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
        value = self.data[self.pos:self.pos + length]
        self.pos += length
        self.pos = (self.pos + 3) & ~3
        return value

    def string_array(self) -> list[StringSpan]:
        count = self.i32()
        if count < 0 or count > 1_000_000:
            raise I2ParseError(f"bad array count {count} at {self.pos - 4}")
        return [self.string() for _ in range(count)]


def _parse_header(data: bytes) -> int:
    """Return the offset just past the MonoBehaviour header and m_Name."""
    reader = _Reader(data)
    game_object_file_id = reader.i32()
    game_object_path_id = struct.unpack_from("<q", data, reader.pos)[0]
    reader.pos += 8
    del game_object_file_id, game_object_path_id
    enabled = data[reader.pos]
    reader.pos = (reader.pos + 4) & ~3
    del enabled
    script_file_id = reader.i32()
    script_path_id = struct.unpack_from("<q", data, reader.pos)[0]
    reader.pos += 8
    del script_file_id, script_path_id
    name = reader.string()
    if name.text != ASSET_NAME:
        raise I2ParseError(f"object is not {ASSET_NAME!r}: {name.text!r}")
    return reader.pos


def parse_table(raw: bytes) -> I2Table:
    """Parse the I2Languages raw object data, asserting the known layout."""
    name_end = _parse_header(raw)
    pre_terms_offset = name_end

    # The pre-region layout is fixed in this build: two zero int32s and
    # one int32 == 1, then the term count. Preserved verbatim on rebuild.
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
    gap_bytes = raw[post_terms_offset:post_terms_offset + 12]
    gap.pos += 12
    language_count = gap.i32()
    languages: list[tuple[str, str, int]] = []
    for _ in range(language_count):
        name = gap.string()
        code = gap.string()
        flags = gap.i32()
        languages.append((name.text, code.text, flags))
    case_sensitive_terms = raw[gap.pos]
    gap.pos = (gap.pos + 4) & ~3
    case_sensitive_categories = raw[gap.pos]
    gap.pos = (gap.pos + 4) & ~3
    del case_sensitive_terms, case_sensitive_categories
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
    """Player-visible table values only: non-empty and not a pure reference."""
    return bool(text.strip()) and not _REFERENCE_RE.match(text)


def apply_translations(raw: bytes, table: I2Table, translations: dict[str, str]) -> bytes:
    """Return the patched raw block with translated English column values.

    Edits are applied from the end of the block backwards so earlier
    spans stay valid. Each edit rewrites the length prefix together with
    the content, keeping every other byte identical.
    """
    by_term = table.term_map()
    edits: list[tuple[int, int, str, bytes]] = []
    for term_key, target in translations.items():
        if term_key not in by_term:
            raise I2ParseError(f"unknown I2 term: {term_key}")
        span = by_term[term_key].values[0]
        content = target.encode("utf-8")
        # Serialized strings are 4-byte aligned: the prefix stores the
        # content length; padding bytes follow so the next field keeps
        # its alignment.
        new_bytes = (
            struct.pack("<i", len(content))
            + content
            + b"\x00" * ((-len(content)) % 4)
        )
        edits.append((span.prefix_offset, span.padded_end, term_key, new_bytes))
    for prefix_offset, edit_end, _term_key, new_bytes in sorted(edits, reverse=True):
        raw = raw[:prefix_offset] + new_bytes + raw[edit_end:]
    return raw


class I2MonoFormat:
    """Handler surface for the I2Languages table inside resources.assets."""

    ASSET_PATH = ASSET_PATH
    ASSET_NAME = ASSET_NAME

    @classmethod
    def discover(cls, input_root: Path) -> list[str]:
        asset = input_root / ASSET_PATH
        if not asset.is_file():
            return []
        try:
            import UnityPy

            env = UnityPy.load(str(asset))
            for obj in env.objects:
                if obj.type.name != "MonoBehaviour":
                    continue
                try:
                    if parse_table(obj.get_raw_data()) is not None:
                        return [ASSET_PATH]
                except I2ParseError:
                    continue
        except Exception:  # noqa: BLE001 - discovery must not crash on foreign assets
            return []
        return []

    @staticmethod
    def find_object(env):
        """Return the ObjectReader of the I2Languages MonoBehaviour."""
        for obj in env.objects:
            if obj.type.name != "MonoBehaviour":
                continue
            try:
                name_end = _parse_header(obj.get_raw_data())
            except I2ParseError:
                continue
            # _parse_header asserts the object name, so reaching here
            # means this is the I2Languages object.
            del name_end
            return obj
        return None

    @classmethod
    def extract(cls, input_root: Path, relpath: str) -> tuple[list[TranslationUnit], dict]:
        import UnityPy

        asset = input_root / ASSET_PATH
        env = UnityPy.load(str(asset))
        obj = cls.find_object(env)
        if obj is None:
            raise ValueError(f"{ASSET_NAME!r} not found in {ASSET_PATH}")
        table = parse_table(obj.get_raw_data())

        units: list[TranslationUnit] = []
        spans: dict[str, tuple[int, int, int]] = {}
        for entry in table.terms:
            span = entry.values[0]
            if not is_translatable(span.text):
                continue
            unit_id = f"i2:{entry.term}"
            units.append(
                TranslationUnit(
                    unit_id=unit_id,
                    source=span.text,
                    source_hash=f"{len(span.text)}:{span.text[:24]}",
                    path=relpath,
                    context={"key": entry.term, "table": ASSET_NAME},
                )
            )
            spans[unit_id] = (span.prefix_offset, span.content_start, span.content_end)
        return units, spans

    @classmethod
    def write_asset(cls, asset_path: Path, translations: dict[str, str]) -> dict:
        """Patch the I2 table of an existing .assets file in place.

        Returns a report: {path_id, terms, patched}. Raises on unknown
        terms so a stale translation map never silently writes garbage.
        """
        import UnityPy

        env = UnityPy.load(str(asset_path))
        obj = cls.find_object(env)
        if obj is None:
            raise ValueError(f"{ASSET_NAME!r} not found in {asset_path}")
        raw = obj.get_raw_data()
        table = parse_table(raw)
        by_term = table.term_map()
        changed = sum(
            1
            for term, value in translations.items()
            if by_term[term].values[0].text != value
        )
        patched = apply_translations(raw, table, translations)
        obj.set_raw_data(patched)

        parent = asset_path.parent
        parent.mkdir(parents=True, exist_ok=True)
        tmp_dir = tempfile.mkdtemp(prefix=".i2save-", dir=str(parent))
        try:
            env.save(pack="none", out_path=tmp_dir)
            produced = Path(tmp_dir) / asset_path.name
            if not produced.is_file():
                raise ValueError(f"UnityPy save did not produce {asset_path.name}")
            os.replace(produced, asset_path)
        finally:
            import shutil

            shutil.rmtree(tmp_dir, ignore_errors=True)
        return {
            "path_id": obj.path_id,
            "terms": table.term_count,
            "patched": len(translations),
            "changed": changed,
        }

    @classmethod
    def read_table(cls, asset_path: Path) -> I2Table:
        """Load and parse the I2 table from an .assets file on disk."""
        import UnityPy

        env = UnityPy.load(str(asset_path))
        obj = cls.find_object(env)
        if obj is None:
            raise ValueError(f"{ASSET_NAME!r} not found in {asset_path}")
        return parse_table(obj.get_raw_data())

    @classmethod
    def validate_table(cls, asset_path: Path, translations: dict[str, str]) -> list[str]:
        """Re-parse the saved table and assert every translated value."""
        problems: list[str] = []
        table = cls.read_table(asset_path)
        for term_key, expected in translations.items():
            entry = table.term_map().get(term_key)
            if entry is None:
                problems.append(f"term vanished after save: {term_key}")
                continue
            actual = entry.values[0].text
            if actual != expected:
                problems.append(f"{term_key}: expected {expected!r}, found {actual!r}")
        return problems
