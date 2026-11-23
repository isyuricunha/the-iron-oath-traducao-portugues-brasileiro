"""Pure-Python HJSON handler for The Iron Oath StreamingAssets data files.

Values are edited surgically by byte/character span on decoded UTF-8 text so
CRLF, tabs, quote style, key order, and comments survive untouched.
"""
from __future__ import annotations

import re
from pathlib import Path

from context import analyze_text_context

BASE_DIRECTORY = "The Iron Oath_Data/StreamingAssets/Data"

SKIP_FILES_RE = re.compile(r"(PreCalculated|DontModify)", re.IGNORECASE)
KEY_DENY = {
    "uniqueid", "id", "icon", "sprite", "spritename", "prefab", "script",
    "guid", "path", "file", "animation", "texture", "sound", "music",
    "settings", "eventid", "catalyst", "partymakeupreq", "modtype", "loot",
    "job", "resists", "immunities", "conflictingtraits", "attributeeffect",
    "missionsgenerated", "category", "sex", "levels", "questname", "metadata",
    "classreq", "turnintothiswhenbroken", "repairmat", "requiredperk", "buff",
    "immune", "resist", "effects", "traitsappliedinstantly", "traitsappliedifuntreated",
    "debuffs", "reward", "mod", "image", "defaultcity", "religion", "banner",
    "colors", "neighbors", "excludedevents", "knownforgear", "commonclasstorecruit",
    "resources", "canleadtoevent", "eventicon", "storyprereq", "otherprereq",
    "loyaltyquest", "goal", "traits", "otherreligionrelationship", "traitsforreligiouspeople",
}
PATH_DENY = ("Dungeons/CombatGroupings",)
DEFAULT_FILE_KEY_DENY = {
    "WorldSim/traits.json": {"name"},
    "WorldSim/opinionModifiers.json": {"name"},
    "Systems/injuries.json": {"name"},
}
DEFAULT_SINGLE_WORD_ALLOW = {
    "Items/Loot/": {"name"},
    "Systems/PlayerJobs.json": {"name"},
    "Systems/DungeonModData.json": {"name"},
    "Dungeons/TileConfigs/TimeMods.json": {"name"},
    "WorldSim/worldEvents.json": {"eventname"},
    "WorldSim/religions.json": {"name"},
    "Systems/injuries.json": {"description"},
}

VALUE_ID_RE = re.compile(r"[_/\\]|[0-9]{2,}|://")
CAMEL_ID_RE = re.compile(r"^[a-z0-9]+(?:[A-Z][a-z0-9]+)+$")


def parse_hjson_object(obj_text: str):
    """Pure-Python lenient HJSON parser matching reference hjson.loads() on game data."""
    s = obj_text
    n = len(s)

    def skip_ws_comments(i: int) -> int:
        while i < n:
            c = s[i]
            if c in " \t\r\n,":
                i += 1
            elif c == "#":
                while i < n and s[i] != "\n":
                    i += 1
            elif c == "/" and i + 1 < n and s[i + 1] == "/":
                while i < n and s[i] != "\n":
                    i += 1
            elif c == "/" and i + 1 < n and s[i + 1] == "*":
                end = s.find("*/", i + 2)
                i = n if end < 0 else end + 2
            else:
                break
        return i

    def read_quoted(i: int, quote: str) -> tuple[str, int]:
        i += 1
        out: list[str] = []
        escapes = {
            '"': '"', "'": "'", "\\": "\\", "/": "/",
            "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t",
        }
        while i < n:
            ch = s[i]
            if ch == "\\" and i + 1 < n:
                nxt = s[i + 1]
                if nxt in escapes:
                    out.append(escapes[nxt])
                    i += 2
                elif nxt == "u" and i + 5 < n:
                    out.append(chr(int(s[i + 2 : i + 6], 16)))
                    i += 6
                else:
                    out.append(nxt)
                    i += 2
            elif ch == quote:
                return "".join(out), i + 1
            else:
                out.append(ch)
                i += 1
        raise ValueError("unterminated string in HJSON")

    def read_key(i: int) -> tuple[str, int]:
        if s[i] in ('"', "'"):
            return read_quoted(i, s[i])
        start = i
        while i < n and s[i] not in " \t\r\n:{},[]":
            i += 1
        return s[start:i], i

    def coerce_scalar(tok: str):
        if tok == "true":
            return True
        if tok == "false":
            return False
        if tok == "null":
            return None
        if re.fullmatch(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?", tok):
            return float(tok) if ("." in tok or "e" in tok or "E" in tok) else int(tok)
        return tok

    def read_val(i: int):
        i = skip_ws_comments(i)
        if i >= n:
            raise ValueError("unexpected EOF in HJSON")
        ch = s[i]
        if ch == "{":
            obj: dict = {}
            i += 1
            while True:
                i = skip_ws_comments(i)
                if i >= n:
                    raise ValueError("unterminated object in HJSON")
                if s[i] == "}":
                    return obj, i + 1
                k, i = read_key(i)
                i = skip_ws_comments(i)
                if i >= n or s[i] != ":":
                    raise ValueError(f"expected ':' after key {k!r} at offset {i}")
                v, i = read_val(i + 1)
                obj[k] = v
        if ch == "[":
            arr: list = []
            i += 1
            while True:
                i = skip_ws_comments(i)
                if i >= n:
                    raise ValueError("unterminated array in HJSON")
                if s[i] == "]":
                    return arr, i + 1
                v, i = read_val(i)
                arr.append(v)
        if ch in ('"', "'"):
            return read_quoted(i, ch)
        start = i
        while i < n and s[i] not in "\r\n,}":
            if s[i] == "]":
                break
            if s[i] == "/" and i + 1 < n and s[i + 1] in "/*":
                break
            if s[i] == "#":
                break
            i += 1
        raw_tok = s[start:i].strip()
        if raw_tok.endswith(","):
            raw_tok = raw_tok[:-1].strip()
        return coerce_scalar(raw_tok), i

    val, end_pos = read_val(0)
    tail = skip_ws_comments(end_pos)
    if tail != n:
        raise ValueError(f"trailing HJSON tokens at offset {tail}")
    return val


class HjsonFormat:
    BASE_DIRECTORY = BASE_DIRECTORY

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

    @staticmethod
    def split_objects(text: str) -> list[tuple[int, int]]:
        spans: list[tuple[int, int]] = []
        depth = 0
        start = None
        in_str = False
        quote_char = '"'
        escaped = False
        for i, c in enumerate(text):
            if in_str:
                if escaped:
                    escaped = False
                elif c == "\\":
                    escaped = True
                elif c == quote_char:
                    in_str = False
            else:
                if c in ('"', "'"):
                    in_str = True
                    quote_char = c
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
            quote_char = '"'
            escaped = False
            i = pos
            while i < n:
                ch = text[i]
                if in_str:
                    if escaped:
                        escaped = False
                    elif ch == "\\":
                        escaped = True
                    elif ch == quote_char:
                        in_str = False
                else:
                    if ch in ('"', "'"):
                        in_str = True
                        quote_char = ch
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

    @staticmethod
    def locate(text: str, obj_start: int, path: list, expected: str | None = None) -> tuple[int, int] | None:
        if path and path[-1][0] == "idx" and expected is not None:
            parent = HjsonFormat.locate(text, obj_start, path[:-1])
            if parent is not None:
                child = HjsonFormat._nth_element(text, parent[0], path[-1][1])
                if child is not None:
                    extent = HjsonFormat._value_extent(text, child)
                    if extent is not None and HjsonFormat._span_matches(text, extent, expected):
                        return extent
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
                val_start = HjsonFormat._value_start(text, window_start + match.end())
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
                    return HjsonFormat._value_extent(text, child)
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
        n = len(text)
        if start >= n or text[start] not in "[{":
            return None
        if index == 0:
            return HjsonFormat._value_start(text, start + 1)
        depth = 0
        in_str = False
        quote_char = '"'
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
                elif ch == quote_char:
                    in_str = False
            else:
                if ch in ('"', "'"):
                    in_str = True
                    quote_char = ch
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

    @classmethod
    def extract(cls, input_root: Path, relpath: str, policy: dict | None = None) -> tuple[list[dict], dict]:
        """Return (records, spans) for one HJSON file."""
        if any(d in relpath for d in PATH_DENY):
            return [], {}
        full = input_root / relpath
        decoded = full.read_bytes().decode("utf-8-sig")
        records: list[dict] = []
        spans: dict[str, tuple[int, int]] = {}
        objects = cls.split_objects(decoded)
        for obj_index, (obj_start, obj_end) in enumerate(objects):
            obj_text = decoded[obj_start:obj_end]
            sanitized = re.sub(r"\b0x[0-9a-fA-F]+\b", lambda m: '"' + m.group(0) + '"', obj_text)
            try:
                data = parse_hjson_object(sanitized)
            except Exception as exc:
                raise ValueError(f"{relpath}: object {obj_index} is not parseable HJSON: {exc}") from exc
            cls._walk(data, [], obj_start, obj_index, relpath, decoded, data if isinstance(data, dict) else {}, records, spans, policy)
        return records, spans

    @classmethod
    def _walk(
        cls,
        node,
        path: list,
        obj_start: int,
        obj_index: int,
        relpath: str,
        decoded: str,
        root_obj: dict,
        records: list[dict],
        spans: dict[str, tuple[int, int]],
        policy: dict | None,
    ) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                cls._walk(value, path + [("key", key)], obj_start, obj_index, relpath, decoded, root_obj, records, spans, policy)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                cls._walk(item, path + [("idx", i)], obj_start, obj_index, relpath, decoded, root_obj, records, spans, policy)
        elif isinstance(node, str):
            keypath = cls._path_key(path)
            if not cls._translatable_key(keypath, relpath, policy):
                return
            allow_single = cls._allows_single_word(keypath, relpath, policy)
            if not cls._translatable(node, allow_single=allow_single):
                return
            span = cls.locate(decoded, obj_start, path, node)
            if span is None:
                raise ValueError(f"{relpath}:{obj_index}:{keypath} value span not found")
            if not cls._span_matches(decoded, span, node):
                return
            unit_id = f"hjson:{relpath}:{obj_index}:{keypath}"
            if unit_id in spans:
                return
            spans[unit_id] = span
            key_name = path[-1][1] if path and path[-1][0] == "key" else (path[-2][1] if len(path) >= 2 and path[-2][0] == "key" else keypath)
            ctx = analyze_text_context(
                node,
                kind="hjson",
                table=relpath,
                key=keypath,
                explicit_sex=str(root_obj.get("Sex") or "") or None,
                class_req=root_obj.get("ClassReq") if isinstance(root_obj.get("ClassReq"), list) else None,
            )
            ctx["object"] = obj_index
            ctx["key"] = key_name
            if root_obj.get("UniqueName"):
                ctx["entry_id"] = str(root_obj["UniqueName"])
            elif root_obj.get("UniqueId"):
                ctx["entry_id"] = str(root_obj["UniqueId"])
            elif root_obj.get("Id"):
                ctx["entry_id"] = str(root_obj["Id"])
            if root_obj.get("QuestName"):
                ctx["quest"] = str(root_obj["QuestName"])
            records.append(
                {
                    "id": unit_id,
                    "row": len(records),
                    "source_file": relpath,
                    "format": "hjson",
                    "key": f"{obj_index}:{keypath}",
                    "context": ctx,
                    "EN": node,
                    "PTBR": "",
                }
            )

    @staticmethod
    def _allows_single_word(keypath: str, relpath: str, policy: dict | None = None) -> bool:
        last = keypath.split(".")[-1].split("[")[0].lower()
        table = (policy or {}).get("hjson_single_word_allow", DEFAULT_SINGLE_WORD_ALLOW)
        for prefix, keys in table.items():
            if prefix in relpath and last in {k.lower() for k in keys}:
                return True
        return False

    @staticmethod
    def _translatable(value: str, *, allow_single: bool = False) -> bool:
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
        if not allow_single and " " not in s:
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
        start, end = extent
        escaped = expected.replace("\\", "\\\\").replace('"', '\\"')
        candidates = [f'"{escaped}"', f"'{escaped}'"]
        if " " not in expected and "'" not in expected and '"' not in expected:
            candidates.append(expected)
        for form in candidates:
            idx = text.find(form, start, end)
            if idx >= 0:
                return idx, idx + len(form)
        return None

    @staticmethod
    def _span_matches(decoded: str, span: tuple[int, int], expected: str) -> bool:
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
    def _translatable_key(keypath: str, relpath: str = "", policy: dict | None = None) -> bool:
        last = keypath.split(".")[-1].split("[")[0].lower()
        if last in KEY_DENY:
            return False
        deny_map = (policy or {}).get("hjson_file_key_deny", DEFAULT_FILE_KEY_DENY)
        for file_part, keys in deny_map.items():
            if file_part in relpath and last in {k.lower() for k in keys}:
                return False
        return True

    @classmethod
    def rebuild(cls, input_root: Path, relpath: str, translations_by_id: dict[str, str], policy: dict | None = None) -> bytes:
        full = input_root / relpath
        raw = full.read_bytes()
        bom = raw.startswith(b"\xef\xbb\xbf")
        decoded = raw.decode("utf-8-sig")
        records, spans = cls.extract(input_root, relpath, policy=policy)
        edits: list[tuple[int, int, str]] = []
        for rec in records:
            uid = rec["id"]
            value = translations_by_id.get(uid)
            if value is None:
                continue
            span = spans[uid]
            quote = decoded[span[0]]
            escaped = (
                value.replace("\\", "\\\\")
                .replace('"', '\\"')
                .replace("\r", "\\r")
                .replace("\n", "\\n")
                .replace("\t", "\\t")
            )
            replacement = f"{quote}{escaped}{quote}" if quote in ('"', "'") else f'"{escaped}"'
            edits.append((span[0], span[1], replacement))
        for start, end, replacement in sorted(edits, reverse=True):
            decoded = decoded[:start] + replacement + decoded[end:]
        # Validate every top-level object remains valid HJSON
        for obj_idx, (obj_s, obj_e) in enumerate(cls.split_objects(decoded)):
            san = re.sub(r"\b0x[0-9a-fA-F]+\b", lambda m: '"' + m.group(0) + '"', decoded[obj_s:obj_e])
            try:
                parse_hjson_object(san)
            except Exception as exc:
                raise ValueError(f"{relpath}: rebuilt object {obj_idx} invalid HJSON: {exc}") from exc
        data = decoded.encode("utf-8")
        return (b"\xef\xbb\xbf" + data) if bom else data
