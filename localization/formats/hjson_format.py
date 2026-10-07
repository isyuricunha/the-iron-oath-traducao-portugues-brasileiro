"""HJSON handler for the Iron Oath StreamingAssets data files.

The files are human-readable JSON (unquoted keys, trailing commas) with
several root objects concatenated per file. Values are edited SURGICALLY
by span on the decoded text: every byte outside the replaced value stays
untouched, so CRLF, tabs, quote style, key order and indentation survive.
Lossless reconstruction is proven by an exact round-trip test.
"""

from __future__ import annotations

import re
from pathlib import Path

import hjson

from ..models import TranslationUnit

BASE_DIRECTORY = "The Iron Oath_Data/StreamingAssets/Data"

SKIP_FILES_RE = re.compile(r"(PreCalculated|DontModify)", re.IGNORECASE)
KEY_DENY = {
    "uniqueid", "id", "icon", "sprite", "spritename", "prefab", "script",
    "guid", "path", "file", "animation", "texture", "sound", "music",
    "settings",
}
VALUE_ID_RE = re.compile(r"[_/\\]|[0-9]{2,}|://")
CAMEL_ID_RE = re.compile(r"^[a-z0-9]+(?:[A-Z][a-z0-9]+)+$")


class HjsonFormat:
    @classmethod
    def discover(cls, input_root: Path) -> list[str]:
        base = input_root / BASE_DIRECTORY
        if not base.is_dir():
            return []
        paths: list[str] = []
        for p in sorted(base.rglob("*.json")):
            rel = p.relative_to(input_root).as_posix()
            if SKIP_FILES_RE.search(rel):
                continue
            paths.append(rel)
        return paths

    # ---------------- parsing ----------------

    @staticmethod
    def split_objects(text: str) -> list[tuple[int, int]]:
        """Absolute spans (start, end) of top-level HJSON objects."""
        spans: list[tuple[int, int]] = []
        depth = 0
        start = None
        in_str = False
        escaped = False
        for i, c in enumerate(text):
            if in_str:
                if escaped:
                    escaped = False
                elif c == "\\":
                    escaped = True
                elif c == '"':
                    in_str = False
            else:
                if c == '"':
                    in_str = True
                elif c == "{":
                    if depth == 0:
                        start = i
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0 and start is not None:
                        spans.append((start, i + 1))
                        start = None
        return spans

    @staticmethod
    def _value_extent(text: str, pos: int) -> tuple[int, int] | None:
        """Extent of the value that starts at pos (right after a colon)."""
        n = len(text)
        while pos < n and text[pos] in " \t\r\n":
            pos += 1
        if pos >= n:
            return None
        c = text[pos]
        if c in ('"', "'"):
            quote = c
            i = pos + 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == quote:
                    return pos, i + 1
                i += 1
            return None
        if c in "[{":
            depth = 0
            in_str = False
            escaped = False
            i = pos
            while i < n:
                ch = text[i]
                if in_str:
                    if escaped:
                        escaped = False
                    elif ch == "\\":
                        escaped = True
                    elif ch == '"':
                        in_str = False
                else:
                    if ch == '"':
                        in_str = True
                    elif ch in "[{":
                        depth += 1
                    elif ch in "]}":
                        depth -= 1
                        if depth == 0:
                            return pos, i + 1
                i += 1
            return None
        end = pos
        while end < n and text[end] not in "\r\n":
            end += 1
        end -= 1
        while end > pos and text[end] in " \t,":
            end -= 1
        return pos, end + 1

    # ---------------- locating values by key path ----------------

    @staticmethod
    def locate(
        text: str,
        obj_start: int,
        path: list,
        expected: str | None = None,
    ) -> tuple[int, int] | None:
        """Find the final value extent (absolute offsets) for a key path.

        When the final segment is an array index, the expected parsed
        value is located literally inside the parent container, which
        handles HJSON's comma-less adjacent-string elements.
        """
        if path and path[-1][0] == "idx" and expected is not None:
            parent = HjsonFormat.locate(text, obj_start, path[:-1])
            if parent is None:
                return None
            literal = HjsonFormat._literal_search(text, parent, expected)
            if literal is not None:
                return literal
        window_start = obj_start
        for idx, segment in enumerate(path):
            if segment[0] == "key":
                key = segment[1]
                match = re.search(
                    rf'(?:^|[\s{{,"\'])(?:"?{re.escape(key)}"?\s*:)',
                    text[window_start:],
                )
                if match is None:
                    return None
                val_start = HjsonFormat._value_start(
                    text, window_start + match.end()
                )
                extent = HjsonFormat._value_extent(text, val_start)
                if extent is None:
                    return None
                if idx == len(path) - 1:
                    return extent
                window_start = extent[0]
            else:
                index = segment[1]
                child = HjsonFormat._nth_element(text, window_start, index)
                if child is None:
                    return None
                if idx == len(path) - 1:
                    extent = HjsonFormat._value_extent(text, child)
                    return extent
                window_start = child
        return None

    @staticmethod
    def _value_start(text: str, pos: int) -> int:
        n = len(text)
        while pos < n and text[pos] in " \t\r\n":
            pos += 1
        return pos

    @staticmethod
    def _nth_element(text: str, start: int, index: int) -> int | None:
        """Start of the index-th value inside the array/object at start.

        Scans elements separated by commas at depth 0 (inside the
        container whose opening bracket sits at `start`).
        """
        n = len(text)
        if start >= n or text[start] not in "[{":
            return None
        if index == 0:
            return HjsonFormat._value_start(text, start + 1)
        depth = 0
        in_str = False
        escaped = False
        found = 0
        i = start + 1
        while i < n:
            ch = text[i]
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch in "[{":
                    depth += 1
                elif ch in "]}":
                    if depth == 0:
                        return None
                    depth -= 1
                elif ch == "," and depth == 0:
                    found += 1
                    if found == index:
                        return HjsonFormat._value_start(text, i + 1)
            i += 1
        return None

    # ---------------- extraction ----------------

    @classmethod
    def extract(cls, input_root: Path, relpath: str) -> tuple[list[TranslationUnit], dict]:
        """Return (units, spans) where spans maps unit_id -> (start, end)."""
        full = input_root / relpath
        raw = full.read_bytes()
        decoded = raw.decode("utf-8-sig")
        units: list[TranslationUnit] = []
        spans: dict[str, tuple[int, int]] = {}
        objects = cls.split_objects(decoded)
        for obj_index, (obj_start, obj_end) in enumerate(objects):
            obj_text = decoded[obj_start:obj_end]
            # The game ships hex color literals (0xRRGGBB) that the HJSON
            # parser rejects; quote them for parsing only. The walk and
            # span math still use the original decoded text.
            sanitized = re.sub(
                r"\b0x[0-9a-fA-F]+\b", lambda m: '"' + m.group(0) + '"', obj_text
            )
            try:
                data = hjson.loads(sanitized)
            except Exception:
                raise ValueError(f"{relpath}: object {obj_index} is not parseable HJSON")
            cls._walk(data, [], obj_start, relpath, obj_index, decoded, units, spans)
        return units, spans

    @classmethod
    def _walk(
        cls,
        node,
        path: list,
        obj_start: int,
        relpath: str,
        obj_index: int,
        decoded: str,
        units: list[TranslationUnit],
        spans: dict[str, tuple[int, int]],
    ) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                cls._walk(value, path + [("key", key)], obj_start, relpath, obj_index, decoded, units, spans)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                cls._walk(item, path + [("idx", i)], obj_start, relpath, obj_index, decoded, units, spans)
        elif isinstance(node, str):
            if not cls._translatable(node):
                return
            keypath = cls._path_key(path)
            if not cls._translatable_key(keypath):
                return
            span = cls.locate(decoded, obj_start, path, node)
            if span is None:
                raise ValueError(f"{relpath}:{obj_index}:{keypath} value span not found")
            if not cls._span_matches(decoded, span, node):
                # Concatenated HJSON strings resolve to one parsed value
                # whose physical span is ambiguous. Never translate it.
                return
            unit_id = f"hjson:{relpath}:{obj_index}:{keypath}"
            if unit_id in spans:
                # Duplicate value inside one container: only the first
                # occurrence is translated; the file stays valid.
                return
            spans[unit_id] = span
            unit = TranslationUnit(
                unit_id=unit_id,
                source=node,
                source_hash=f"{len(node)}:{node[:24]}",
                path=relpath,
                context={
                    "object": obj_index,
                    "key": path[-1][1] if path and path[-1][0] == "key" else "",
                    "siblings": cls._siblings(decoded, obj_start, span),
                },
            )
            units.append(unit)

    @staticmethod
    def _siblings(decoded: str, obj_start: int, span: tuple[int, int]) -> str:
        """Bounded sibling context pulled from the enclosing object."""
        start = max(obj_start, span[0] - 260)
        end = min(len(decoded), span[1] + 120)
        return decoded[start:end].replace("\r", " ").replace("\n", " ")[:360]

    @staticmethod
    def _translatable(value: str) -> bool:
        s = value.strip()
        if len(s) < 2:
            return False
        if not re.search(r"[A-Za-z]", s):
            return False
        if VALUE_ID_RE.search(s) and " " not in s:
            return False
        if " " not in s and re.search(r"\d", s):
            return False
        if " " not in s and re.search(r"[a-z][A-Z]", s):
            return False
        if " " not in s and s == s.lower():
            return False
        return not (CAMEL_ID_RE.match(s) and not s[0].isupper())

    @staticmethod
    def _path_key(path: list) -> str:
        parts: list[str] = []
        for seg in path:
            if seg[0] == "key":
                parts.append(seg[1])
            elif parts:
                parts[-1] += f"[{seg[1]}]"
        return ".".join(parts)

    @staticmethod
    def _literal_search(text: str, extent: tuple[int, int], expected: str) -> tuple[int, int] | None:
        """Find the quoted literal of `expected` inside a container extent.

        Searches `"value"`, `'value'`, then the bare value. HJSON string
        concatenation and escapes are ignored here: the caller validates
        the found span with _span_matches before accepting it.
        """
        start, end = extent
        candidates: list[str] = []
        escaped = expected.replace("\\", "\\\\").replace('"', '\\"')
        candidates.append(f'"{escaped}"')
        candidates.append(f"'{escaped}'")
        if " " not in expected and "'" not in expected and '"' not in expected:
            candidates.append(expected)
        for form in candidates:
            idx = text.find(form, start, end)
            if idx >= 0:
                return idx, idx + len(form)
        return None

    @staticmethod
    def _span_matches(decoded: str, span: tuple[int, int], expected: str) -> bool:
        """True when the located span decodes to exactly the parsed value.

        Concatenated quoted strings (HJSON `"a""b"`) parse as one value
        but their physical span only covers the first literal: detected
        here and skipped so rebuilds can never corrupt the file.
        """
        raw = decoded[span[0] : span[1]]
        if raw[:1] in ('"', "'"):
            if raw[-1:] != raw[:1]:
                return False
            body = raw[1:-1]
            body = (
                body.replace('\\"', '"')
                .replace("\\\\", "\\")
                .replace("\\n", "\n")
                .replace("\\r", "\r")
                .replace("\\t", "\t")
            )
            return body == expected
        return raw.strip() == expected

    @staticmethod
    def _translatable_key(keypath: str) -> bool:
        if keypath.lower() == "id" or keypath.lower().startswith("uniqueid"):
            return False
        last = keypath.split(".")[-1].lower()
        return last not in KEY_DENY

    # ---------------- rebuild & validation ----------------

    @classmethod
    def rebuild(
        cls, input_root: Path, relpath: str, translations_by_id: dict[str, str]
    ) -> bytes:
        """Return rebuilt file bytes with translations applied by span."""
        full = input_root / relpath
        raw = full.read_bytes()
        bom = raw.startswith(b"\xef\xbb\xbf")
        decoded = raw.decode("utf-8-sig")
        units, spans = cls.extract(input_root, relpath)
        edits: list[tuple[int, int, str]] = []
        for unit in units:
            value = translations_by_id.get(unit.unit_id)
            if value is None:
                continue
            span = spans[unit.unit_id]
            quote = decoded[span[0]]
            escaped = (
                value.replace("\\", "\\\\")
                .replace('"', '\\"')
                .replace("\r", "\\r")
                .replace("\n", "\\n")
                .replace("\t", "\\t")
            )
            if quote in ('"', "'"):
                replacement = f'{quote}{escaped}{quote}'
            else:
                replacement = f'"{escaped}"'
            edits.append((span[0], span[1], replacement))
        for start, end, replacement in sorted(edits, reverse=True):
            decoded = decoded[:start] + replacement + decoded[end:]
        data = decoded.encode("utf-8")
        if bom:
            data = b"\xef\xbb\xbf" + data
        return data

    @classmethod
    def validate_file(cls, input_root: Path, relpath: str) -> int:
        units, _ = cls.extract(input_root, relpath)
        return len(units)

    @classmethod
    def apply(
        cls, input_root: Path, relpath: str, translations_by_id: dict[str, str]
    ) -> bytes:
        return cls.rebuild(input_root, relpath, translations_by_id)