"""Structural token, tag, delimiter, and escape validation for PT-BR localization."""
from __future__ import annotations

from collections import Counter
import re

TOKEN_RE = re.compile(
    r"\{[^{}]*\}"                                   # {0}, {[PARAM1]}, {*ref*}, {advisor}, {npc:...}
    r"|</?[a-zA-Z][a-zA-Z0-9]*(?:=[^<>]*)?/?>"      # <color=#...>, </color>, <sprite ...>, <b>, <link=...>
    r"|\[[^\[\]]*\]"                                # [FNameP0], [myTitle], [pronounPoss0]
    r"|(?<!\w)%[-+0-9#.]*[diufFeEgGxXoscpaA%]"      # printf specifiers without spaces
    r"|\|NEXTPAGE\|"                                # Codex journal page break
)

# Words that always carry diacritics in standard PT-BR (diagnostic hint only,
# never a hard blocker so migrated diacritic-stripped lines remain valid).
_UNACCENTED_HINT_RE = re.compile(
    r"\b(?:nao|voce|lider|configuracao|traducao|publicacao|informacao|"
    r"educacao|opcao|acao|estrategia|agencia|experiencia|tambem|entao)\b",
    re.IGNORECASE,
)

_TAG_NAME_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)(?:[\s=][^<>]*)?(/?)>")
_SELF_CLOSING_TAGS = {"sprite", "br"}


class SyntaxErrorDetail(ValueError):
    """Raised when a translation breaks structural syntax or placeholders."""


def extract_tokens(text: str) -> Counter[str]:
    """Return multiset of protected technical tokens in text."""
    return Counter(TOKEN_RE.findall(text))


def check_markup_balance(text: str) -> list[str]:
    """Verify paired rich-text tags (<color>, <b>, <i>, <link>) balance cleanly."""
    problems: list[str] = []
    stacks: dict[str, int] = {}
    for match in _TAG_NAME_RE.finditer(text):
        is_close = bool(match.group(1))
        name = match.group(2).lower()
        is_self = bool(match.group(3)) or name in _SELF_CLOSING_TAGS
        if is_self:
            continue
        if is_close:
            cur = stacks.get(name, 0)
            if cur <= 0:
                problems.append(f"tag </{match.group(2)}> closes before opening")
            else:
                stacks[name] = cur - 1
        else:
            stacks[name] = stacks.get(name, 0) + 1
    for name, depth in sorted(stacks.items()):
        if depth > 0:
            problems.append(f"unclosed tag <{name}>")
    return problems


def validate_translation(source: str, target: str) -> list[str]:
    """Return list of hard structural errors (empty when structurally valid)."""
    problems: list[str] = []
    if not isinstance(target, str):
        return ["PTBR must be a string"]
    if source.strip() and not target.strip():
        return ["PTBR cannot be blank for non-empty EN source"]

    expected = extract_tokens(source)
    actual = extract_tokens(target)
    if expected != actual:
        missing = expected - actual
        extra = actual - expected
        if missing:
            problems.append(f"missing tokens: {sorted(missing.elements())[:6]}")
        if extra:
            problems.append(f"unexpected tokens: {sorted(extra.elements())[:6]}")

    for pipe_sep in ("|", "^"):
        if source.count(pipe_sep) != target.count(pipe_sep):
            problems.append(
                f"delimiter {pipe_sep!r} count mismatch: expected {source.count(pipe_sep)}, got {target.count(pipe_sep)}"
            )

    src_balance = check_markup_balance(source)
    tgt_balance = check_markup_balance(target)
    if tgt_balance != src_balance:
        problems.extend(tgt_balance or ["markup nesting changed relative to EN source"])

    if "\x00" in target:
        problems.append("null byte is not allowed in translation")
    return problems


def assert_valid_translation(source: str, target: str) -> None:
    problems = validate_translation(source, target)
    if problems:
        raise SyntaxErrorDetail("; ".join(problems))


def orthography_warnings(target: str) -> list[str]:
    """Non-blocking diagnostic hints for stripped accents in PTBR master."""
    if not target:
        return []
    hits = _UNACCENTED_HINT_RE.findall(target)
    if hits:
        sample = sorted(set(h.lower() for h in hits))[:5]
        return [f"possible unaccented PT-BR word(s): {', '.join(sample)}"]
    return []
