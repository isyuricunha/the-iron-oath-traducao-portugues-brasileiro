"""Yarn Spinner dialogue and quest script handler (.yarn.txt).

Extracts narration, choices, [[text|node]] shortcuts, [Action:<text>] labels,
bracketed [prose] blocks, Speaker: lines, whitelisted <<set $Var>> strings,
<<Show*>> display args, and quest/objective command strings. Preserves all
comments, node headers, labels, commands, and non-display logic verbatim.
"""
from __future__ import annotations

import re
from pathlib import Path

from context import analyze_text_context, parse_createnpc_chunk

BASE_DIRECTORY = "The Iron Oath_Data/StreamingAssets/Data"
YARN_SUFFIX = ".yarn.txt"

_SET_RE = re.compile(r'^<<set\s+\$(\w+)\s+to\s+"([^"]*)"\s*>>\s*$')
_CHOICE_RE = re.compile(r"^(->\s*|\+\s*)(.+)$")
_SPEAKER_RE = re.compile(r"^([A-Z][A-Za-z0-9 ]{0,24}:\s*)(.+)$")

_SET_VAR_WHITELIST = {
    "EventTitle",
    "EventDescription",
    "QuestDescription",
    "QuestIntro",
    "ShortDescription",
    "LongDescription",
    "Objective",
    "ObjectiveText",
}

_COMMAND_SHAPES = (
    re.compile(r'^(\s*<<set \$(?:QuestName|DungeonLocName) to ")([^"]+)(">>)\s*$'),
    re.compile(r'^(\s*<<UpdateQuestObjectiveText ")([^"]+)(">>)\s*$'),
    re.compile(r'^(\s*<<SetPrimaryQuestObjectiveComplete ")([^"]+)(">>)\s*$'),
    re.compile(
        r'^(\s*<<UpdateQuestObjective Quest\.[A-Za-z0-9]+ [a-z]+ '
        r'(?:active|success|failed) ")([^"]+)(">>)\s*$'
    ),
)

_SHORTCUT_RE = re.compile(r"^\[\[([^|\]]+)\|([^\]]+)\]\]$")
_JUMP_ONLY_RE = re.compile(r"^\[\[[^|\[\]]+\]\]$")
_ACTION_LABEL_RE = re.compile(r"^(\[Action:)([^\]]+)(\])$")
_CODE_DIRECTIVE_ONLY_RE = re.compile(r"^\[(?:Locked|Mining|DodgeTrap|Disarm|RequireItem|RemoveRequiredItem|Time):[^\[\]]+\]$")


def _translatable(value: str) -> bool:
    s = value.strip()
    if len(s) < 2 or not re.search(r"[A-Za-z]", s) or re.fullmatch(r"[\W_]+", s):
        return False
    return not (s == s.upper() and len(s.split()) == 1)


class YarnFormat:
    BASE_DIRECTORY = BASE_DIRECTORY

    @classmethod
    def discover(cls, input_root: Path) -> list[str]:
        base = input_root / BASE_DIRECTORY
        if not base.is_dir():
            return []
        return [p.relative_to(input_root).as_posix() for p in sorted(base.rglob(f"*{YARN_SUFFIX}"))]

    @classmethod
    def extract(cls, input_root: Path, relpath: str) -> tuple[list[dict], dict]:
        path = input_root / relpath
        if not path.is_file():
            return [], {}
        decoded = path.read_bytes().decode("utf-8-sig")
        raw_lines = decoded.splitlines(keepends=True)

        # Pre-index chunks by line index to mine CreateNpc metadata & neighbor pronouns per node.
        chunk_info: dict[int, dict] = {}
        chunk_start = 0
        for idx, raw_line in enumerate(raw_lines):
            if raw_line.strip() == "===" or idx == len(raw_lines) - 1:
                end_idx = idx if raw_line.strip() == "===" else idx + 1
                chunk_slice = raw_lines[chunk_start:end_idx]
                npcs = parse_createnpc_chunk(chunk_slice)
                for li in range(chunk_start, end_idx):
                    chunk_info[li] = {"npcs": npcs, "lines": chunk_slice}
                chunk_start = idx + 1

        records: list[dict] = []
        spans: dict[str, tuple[int, int, bool]] = {}
        node = ""
        in_header = True
        node_skip = False
        offset = 0

        for index, raw_line in enumerate(raw_lines):
            eol_len = len(raw_line) - len(raw_line.rstrip("\r\n"))
            content = raw_line[: len(raw_line) - eol_len] if eol_len else raw_line
            stripped = content.strip()
            leading = len(content) - len(content.lstrip())
            start = offset + leading
            end = offset + len(content.rstrip())

            if stripped == "===":
                node = ""
                in_header = True
                node_skip = False
            elif stripped == "---":
                in_header = False
            elif in_header:
                if stripped.lower().startswith("title:"):
                    node = stripped[6:].strip()
                    if node == "IDEA FOR LATER":
                        node_skip = True
                if stripped.lower().startswith("label:"):
                    node_skip = True
            elif node_skip or not stripped or stripped.startswith("//"):
                pass
            elif cls._match_command_shape(content, offset, index, relpath, node, records, spans, chunk_info.get(index)):
                pass
            elif stripped.startswith("<<Show"):
                space = stripped.find(" ", 2)
                family = stripped[2 : space if space != -1 else len(stripped)].strip("<>")
                quoted = list(re.finditer(r'"([^"]*)"', stripped))
                display = quoted[1:] if family == "ShowTutorialMessage" and len(quoted) >= 3 else quoted
                if family in ("ShowTutorialMessage", "ShowAdvisorMessage", "ShowLetter"):
                    for arg_index, match in enumerate(display, start=1):
                        value = match.group(1)
                        if not _translatable(value):
                            continue
                        val_start = start + match.start(1)
                        uid = cls._unit_id(relpath, index, arg_index)
                        spans[uid] = (val_start, val_start + len(value), True)
                        records.append(cls._make_record(uid, relpath, index, node, value, f"{family}:{arg_index}", records, chunk_info.get(index)))
            elif stripped.startswith("<<"):
                match = _SET_RE.match(stripped)
                if match and match.group(1) in _SET_VAR_WHITELIST and _translatable(match.group(2)):
                    val_start = offset + content.index('"', leading) + 1
                    uid = cls._unit_id(relpath, index)
                    spans[uid] = (val_start, val_start + len(match.group(2)), True)
                    records.append(cls._make_record(uid, relpath, index, node, match.group(2), match.group(1), records, chunk_info.get(index)))
            elif (choice := _CHOICE_RE.match(stripped)) is not None:
                choice_body = choice.group(2)
                choice_start = start + len(choice.group(1))
                action_m = _ACTION_LABEL_RE.match(choice_body.strip())
                if action_m:
                    inner = action_m.group(2)
                    if _translatable(inner):
                        inner_start = choice_start + len(action_m.group(1))
                        uid = cls._unit_id(relpath, index)
                        spans[uid] = (inner_start, inner_start + len(inner), False)
                        records.append(cls._make_record(uid, relpath, index, node, inner, "choice_action", records, chunk_info.get(index)))
                elif _CODE_DIRECTIVE_ONLY_RE.match(choice_body.strip()):
                    pass
                elif _translatable(choice_body):
                    uid = cls._unit_id(relpath, index)
                    spans[uid] = (choice_start, end, False)
                    records.append(cls._make_record(uid, relpath, index, node, choice_body, "choice", records, chunk_info.get(index)))
            elif (shortcut := _SHORTCUT_RE.match(stripped)) is not None:
                if _translatable(shortcut.group(1)):
                    text_start = start + 2
                    uid = cls._unit_id(relpath, index)
                    spans[uid] = (text_start, text_start + len(shortcut.group(1)), False)
                    records.append(cls._make_record(uid, relpath, index, node, shortcut.group(1), "choice", records, chunk_info.get(index)))
            else:
                speaker = _SPEAKER_RE.match(stripped)
                if speaker:
                    if _translatable(speaker.group(2)):
                        uid = cls._unit_id(relpath, index)
                        spans[uid] = (start + len(speaker.group(1)), end, False)
                        records.append(cls._make_record(uid, relpath, index, node, speaker.group(2), speaker.group(1).strip(), records, chunk_info.get(index)))
                elif _translatable(stripped):
                    if _JUMP_ONLY_RE.match(stripped) is not None or _CODE_DIRECTIVE_ONLY_RE.match(stripped) is not None:
                        pass
                    elif stripped.startswith("[") and stripped.endswith("]") and "[" not in stripped[1:-1] and ":" not in stripped[1:-1]:
                        # Whole-line bracketed stage prose e.g. "[As you read the contents aloud...]"
                        inner = stripped[1:-1]
                        uid = cls._unit_id(relpath, index)
                        spans[uid] = (start + 1, end - 1, False)
                        records.append(cls._make_record(uid, relpath, index, node, inner, "narration_bracketed", records, chunk_info.get(index)))
                    elif stripped.startswith("[") and stripped.endswith("]") and "[questTown]" in stripped:
                        inner = stripped[1:-1]
                        uid = cls._unit_id(relpath, index)
                        spans[uid] = (start + 1, end - 1, False)
                        records.append(cls._make_record(uid, relpath, index, node, inner, "narration_bracketed", records, chunk_info.get(index)))
                    else:
                        bold = stripped.startswith("**") and stripped.endswith("**") and len(stripped) > 4
                        uid = cls._unit_id(relpath, index)
                        spans[uid] = (start + (2 if bold else 0), end - (2 if bold else 0), False)
                        records.append(
                            cls._make_record(
                                uid, relpath, index, node,
                                stripped[2:-2] if bold else stripped,
                                "narration", records, chunk_info.get(index),
                            )
                        )
            offset += len(raw_line)
        return records, spans

    @classmethod
    def _match_command_shape(
        cls,
        content: str,
        offset: int,
        index: int,
        relpath: str,
        node: str,
        records: list[dict],
        spans: dict,
        chunk: dict | None,
    ) -> bool:
        for shape in _COMMAND_SHAPES:
            m = shape.match(content)
            if m:
                source = m.group(2)
                if not _translatable(source):
                    return True
                val_start = offset + m.start(2)
                uid = f"yarn:{relpath}:{index}:cmd"
                spans[uid] = (val_start, val_start + len(source), True)
                records.append(cls._make_record(uid, relpath, index, node, source, "quest_command", records, chunk))
                return True
        return False

    @staticmethod
    def _unit_id(relpath: str, line_index: int, arg_index: int = 0) -> str:
        if arg_index:
            return f"yarn:{relpath}:{line_index}:a{arg_index}"
        return f"yarn:{relpath}:{line_index}"

    @staticmethod
    def _make_record(
        uid: str,
        relpath: str,
        line_index: int,
        node: str,
        source: str,
        kind: str,
        records: list[dict],
        chunk: dict | None,
    ) -> dict:
        npcs = (chunk or {}).get("npcs", {})
        lines = (chunk or {}).get("lines", [])
        ctx = analyze_text_context(
            source,
            kind=kind,
            node=node,
            table=relpath,
            key=f"{line_index}",
            chunk_npcs=npcs,
            neighbor_lines=[l.strip() for l in lines[:40]],
        )
        ctx["node"] = node
        ctx["kind"] = kind
        return {
            "id": uid,
            "row": len(records),
            "source_file": relpath,
            "format": "yarn",
            "key": f"{line_index}",
            "context": ctx,
            "EN": source,
            "PTBR": "",
        }

    @classmethod
    def rebuild(cls, input_root: Path, relpath: str, translations_by_id: dict[str, str]) -> bytes:
        path = input_root / relpath
        raw = path.read_bytes()
        bom = raw.startswith(b"\xef\xbb\xbf")
        decoded = raw.decode("utf-8-sig")
        records, spans = cls.extract(input_root, relpath)
        edits: list[tuple[int, int, str]] = []
        for rec in records:
            uid = rec["id"]
            value = translations_by_id.get(uid)
            if value is None:
                continue
            start, end, in_quotes = spans[uid]
            text = value.replace("\r", " ").replace("\n", " ").replace("\t", " ").strip()
            if in_quotes:
                text = text.replace('"', '\\"')
            edits.append((start, end, text))
        for start, end, text in sorted(edits, reverse=True):
            decoded = decoded[:start] + text + decoded[end:]
        data = decoded.encode("utf-8")
        return (b"\xef\xbb\xbf" + data) if bom else data
