"""Yarn Spinner dialogue and quest script handler (.yarn.txt).

The game loads these files from StreamingAssets at runtime, so a rebuilt
file with translated display lines is picked up verbatim. Node headers,
comments, separators and commands are structural and never extracted;
only narration, choice lines, spoken lines and whitelisted display
variables inside <<set>> commands are translated. Everything else keeps
its original bytes.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..models import TranslationUnit

BASE_DIRECTORY = "The Iron Oath_Data/StreamingAssets/Data"
YARN_SUFFIX = ".yarn.txt"

_HEADER_RE = re.compile(r"^(?:title|tags|groupName|position):", re.IGNORECASE)
_SET_RE = re.compile(r'^<<set\s+\$(\w+)\s+to\s+"([^"]*)"\s*>>\s*$')
_CHOICE_RE = re.compile(r"^(->\s*|\+\s*)(.+)$")
_SPEAKER_RE = re.compile(r"^([A-Z][A-Za-z0-9 ]{0,24}:\s*)(.+)$")

# <<set>> variables whose values are displayed to the player. Every other
# variable is a reference (node names, enums, locations, numbers) and is
# never touched.
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

# Display-name and objective commands: their quoted arguments are pure
# display text (SetPrimaryQuestObjectiveComplete only forwards the string
# to UpdateQuestObjective, which sets the tracker text), so they can be
# translated by value without touching quest ids or variables.
_COMMAND_SHAPES = (
    re.compile(r'^(\s*<<set \$(?:QuestName|DungeonLocName) to ")([^"]+)(">>)\s*$'),
    re.compile(r'^(\s*<<UpdateQuestObjectiveText ")([^"]+)(">>)\s*$'),
    re.compile(r'^(\s*<<SetPrimaryQuestObjectiveComplete ")([^"]+)(">>)\s*$'),
    re.compile(
        r'^(\s*<<UpdateQuestObjective Quest\.[A-Za-z0-9]+ [a-z]+ '
        r'(?:active|success|failed) ")([^"]+)(">>)\s*$'
    ),
)

_SHORTCUT_RE = re.compile(r'^\[\[([^|\]]+)\|([^\]]+)\]\]$')

# Pure "[[node]]" jump lines: no pipe, no inner bracket.
_JUMP_ONLY_RE = re.compile(r'^\[\[[^|\[\]]+\]\]$')


def _translatable(value: str) -> bool:
    """Yarn body lines are always display text, so the HJSON identifier
    filters do not apply; only numbers, punctuation and single all-caps
    tokens (enums) are refused."""
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
        return [
            p.relative_to(input_root).as_posix()
            for p in sorted(base.rglob(f"*{YARN_SUFFIX}"))
        ]

    @staticmethod
    def _name_of(obj) -> str:  # pragma: no cover - interface parity only
        return ""

    @classmethod
    def extract(cls, input_root: Path, relpath: str) -> tuple[list[TranslationUnit], dict]:
        """Return (units, spans); span values are (start, end, in_quotes).

        Chunks are delimited by === lines. Header lines (before the ---
        separator) are always structural: old-format nodes use label:
        instead of title: and their labels are node keys the game looks
        up, so a chunk with a label: header is skipped entirely and only
        body lines of normal nodes are ever extracted.
        """
        units: list[TranslationUnit] = []
        spans: dict[str, tuple[int, int, bool]] = {}
        path = input_root / relpath
        if not path.is_file():
            return units, spans
        decoded = path.read_bytes().decode("utf-8-sig")
        node = ""
        in_header = True
        node_skip = False
        offset = 0
        for index, raw_line in enumerate(decoded.splitlines(keepends=True)):
            eol_len = len(raw_line) - len(raw_line.rstrip("\r\n"))
            content = raw_line[: len(raw_line) - eol_len] if eol_len else raw_line
            stripped = content.strip()
            start = offset + (len(content) - len(content.lstrip()))
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
                if stripped.lower().startswith("label:"):
                    node_skip = True
            elif node_skip or not stripped or stripped.startswith("//"):
                pass
            elif stripped.startswith("<<Show"):
                # Dialog commands carry display text in quoted args:
                # ShowTutorialMessage ("id", "title", "text"), ShowLetter
                # ("title", "body"), ShowAdvisorMessage ("id", "", "body").
                # With three or more arguments the first is an id and is
                # skipped.
                space = stripped.find(" ", 2)
                family = stripped[2:space if space != -1 else len(stripped)].strip("<>")
                quoted = list(re.finditer(r'"([^"]*)"', stripped))
                display = (
                    quoted[1:]
                    if family == "ShowTutorialMessage" and len(quoted) >= 3
                    else quoted
                )
                if family in ("ShowTutorialMessage", "ShowAdvisorMessage", "ShowLetter"):
                    for arg_index, match in enumerate(display, start=1):
                        value = match.group(1)
                        if not _translatable(value):
                            continue
                        value_start = (
                            offset
                            + (len(content) - len(content.lstrip()))
                            + match.start(1)
                        )
                        unit_id = cls._unit_id(relpath, index, arg_index)
                        spans[unit_id] = (value_start, value_start + len(value), True)
                        units.append(
                            cls._unit(
                                relpath,
                                index,
                                node,
                                value,
                                f"{family}:{arg_index}",
                                arg_index,
                            )
                        )
            elif stripped.startswith("<<"):
                match = _SET_RE.match(stripped)
                if (
                    match
                    and match.group(1) in _SET_VAR_WHITELIST
                    and _translatable(match.group(2))
                ):
                    # The span sits strictly inside the quotes so the
                    # command text itself stays untouched.
                    value_start = offset + content.index('"', start - offset) + 1
                    spans[cls._unit_id(relpath, index)] = (
                        value_start,
                        value_start + len(match.group(2)),
                        True,
                    )
                    units.append(
                        cls._unit(relpath, index, node, match.group(2), match.group(1))
                    )
            elif (choice := _CHOICE_RE.match(stripped)) is not None:
                if _translatable(choice.group(2)):
                    spans[cls._unit_id(relpath, index)] = (
                        start + len(choice.group(1)),
                        end,
                        False,
                    )
                    units.append(
                        cls._unit(relpath, index, node, choice.group(2), "choice")
                    )
            elif (shortcut := _SHORTCUT_RE.match(stripped)) is not None:
                # Whole-line "[[text|node]]" Yarn shortcuts are player
                # choices; the span covers only the text so the link and
                # destination node survive untouched.
                if _translatable(shortcut.group(1)):
                    text_start = start + 2
                    spans[cls._unit_id(relpath, index)] = (
                        text_start,
                        text_start + len(shortcut.group(1)),
                        False,
                    )
                    units.append(
                        cls._unit(relpath, index, node, shortcut.group(1), "choice")
                    )
            else:
                speaker = _SPEAKER_RE.match(stripped)
                if speaker:
                    if _translatable(speaker.group(2)):
                        spans[cls._unit_id(relpath, index)] = (
                            start + len(speaker.group(1)),
                            end,
                            False,
                        )
                        units.append(
                            cls._unit(relpath, index, node, speaker.group(2), speaker.group(1).strip())
                        )
                elif _translatable(stripped):
                    # Bold narration: the ** markers are formatting; the
                    # span covers only the text between them.
                    if _JUMP_ONLY_RE.match(stripped) is not None:
                        # Pure "[[node]]" jumps carry no display text and
                        # must never become translation units: a provider
                        # target would corrupt the jump.
                        pass
                    else:
                        bold = stripped.startswith("**") and stripped.endswith("**") and len(stripped) > 4
                        unit_id = cls._unit_id(relpath, index)
                        spans[unit_id] = (start + (2 if bold else 0), end - (2 if bold else 0), False)
                        units.append(
                            cls._unit(
                                relpath,
                                index,
                                node,
                                stripped[2:-2] if bold else stripped,
                                "narration",
                            )
                        )
            offset += len(raw_line)
        return units, spans

    @staticmethod
    def _unit_id(relpath: str, line_index: int, arg_index: int = 0) -> str:
        if arg_index:
            return f"yarn:{relpath}:{line_index}:a{arg_index}"
        return f"yarn:{relpath}:{line_index}"

    @staticmethod
    def _unit(
        relpath: str,
        line_index: int,
        node: str,
        source: str,
        kind: str,
        arg_index: int = 0,
    ) -> TranslationUnit:
        return TranslationUnit(
            unit_id=YarnFormat._unit_id(relpath, line_index, arg_index),
            source=source,
            source_hash=f"{len(source)}:{source[:24]}",
            path=relpath,
            context={"node": node, "kind": kind},
        )

    @classmethod
    def rebuild(
        cls,
        input_root: Path,
        relpath: str,
        translations_by_id: dict[str, str],
        commands: dict[str, str] | None = None,
    ) -> bytes:
        """Return rebuilt file bytes with translated lines applied by span.

        When a command map is given, quoted display arguments of the
        quest-name and objective commands are replaced by value as well;
        quest ids, slot names and statuses are structural and untouched.
        """
        path = input_root / relpath
        raw = path.read_bytes()
        bom = raw.startswith(b"\xef\xbb\xbf")
        decoded = raw.decode("utf-8-sig")
        units, spans = cls.extract(input_root, relpath)
        edits: list[tuple[int, int, str]] = []
        for unit in units:
            value = translations_by_id.get(unit.unit_id)
            if value is None:
                continue
            start, end, in_quotes = spans[unit.unit_id]
            text = value.replace("\r", " ").replace("\n", " ").replace("\t", " ").strip()
            if in_quotes:
                text = text.replace('"', '\"')
            edits.append((start, end, text))
        # Span edits use offsets computed on the original text, so they
        # must land before the line-anchored command map (which changes
        # line lengths and would invalidate every remaining offset).
        for start, end, text in sorted(edits, reverse=True):
            decoded = decoded[:start] + text + decoded[end:]
        if commands:
            decoded, _ = cls.apply_command_map(decoded, commands)
        data = decoded.encode("utf-8")
        if bom:
            data = b"\xef\xbb\xbf" + data
        return data

    @staticmethod
    def apply_command_map(decoded: str, command_map: dict[str, str]) -> tuple[str, dict]:
        """Replace quoted display args of quest/objective commands by value.

        Returns (patched text, report) where the report counts one patch
        per replaced argument. Lines commented with // are skipped, and
        quest ids, slot names and statuses are structural and untouched.
        """
        lines = decoded.splitlines(keepends=True)
        patched = 0
        for index, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("//"):
                continue
            for shape in _COMMAND_SHAPES:
                match = shape.match(line.rstrip("\r\n"))
                if not match:
                    continue
                source = match.group(2)
                target = command_map.get(source)
                if target is None:
                    break
                clean = target.replace("\r", " ").replace("\n", " ").replace("\t", " ").strip()
                new_line = line.replace(f'"{source}"', f'"{clean}"', 1)
                lines[index] = new_line
                patched += 1
                break
        report = {"patched": patched}
        return "".join(lines), report

    @classmethod
    def validate_rebuilt(cls, data: bytes, command_map: dict[str, str]) -> list[str]:
        """Re-scan rebuilt bytes; no mapped EN argument may survive."""
        problems: list[str] = []
        decoded = data.decode("utf-8-sig")
        for line in decoded.splitlines():
            stripped = line.strip()
            if stripped.startswith("//"):
                continue
            for shape in _COMMAND_SHAPES:
                match = shape.match(line.rstrip("\r\n"))
                if match and match.group(2) in command_map:
                    problems.append(f"command arg still English: {match.group(2)!r}")
        return problems

    @classmethod
    def validate_file(cls, input_root: Path, relpath: str) -> int:
        units, _ = cls.extract(input_root, relpath)
        return len(units)

    @classmethod
    def write_asset(cls, input_root: Path, staging_path: Path, xml: str) -> None:  # pragma: no cover
        raise NotImplementedError("yarn files are written directly by build_staging")

    @classmethod
    def validate_str(cls, xml: str, expected: dict[str, str]) -> int:  # pragma: no cover
        raise NotImplementedError("yarn validation is file-based")
