"""Protected-token handling and target-language text validation.

Vocabulary
----------
* independent: placeholders, variables, hotkeys, numbers; may move as a
  whole for natural grammar.
* paired: balanced opening/closing markup that must stay balanced.
* group: structural sub-tokens whose relative order inside one group is
  immutable.
* fixed: exact-position repository syntax (only when proven by the format).

Validation compares token MULTISETS (Counter equality), then enforces the
category-specific structural rules. Raw values are grammatical context
only: they must never be emitted back in place of their token.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from .models import ProtectedToken, TokenCategory

TOKEN_RE = re.compile(r"__KEEP_(\d+)__")

# Fragments that are protected because they are technical, not translatable.
# Order matters within the combined alternation: longer/more specific
# patterns first (tags and sprites before bare numbers). The alternation
# runs in ONE re.sub pass so generated __KEEP_N__ tokens are never
# rescanned or re-matched.
_PATTERNS: list[tuple[TokenCategory, str | None, str]] = [
    (TokenCategory.INDEPENDENT, "sprite", r"<sprite\s+name=\"[^\"]+\">"),
    (TokenCategory.GROUP, "color", r"<color=#[0-9A-Fa-f]{6}>"),
    (TokenCategory.GROUP, "color", r"</color>"),
    (TokenCategory.PAIRED, None, r"<(/?)[a-zA-Z][a-zA-Z0-9]*>"),
    (TokenCategory.INDEPENDENT, "placeholder", r"\{[^{}]+\}"),
    (TokenCategory.INDEPENDENT, "square", r"\[[^\[\]]+\]"),
    (TokenCategory.INDEPENDENT, "printf", r"%[-+0-9# .]*[a-zA-Z%]"),
    (TokenCategory.INDEPENDENT, "number", r"\d+(?:[.,]\d+)*%?"),
    (TokenCategory.GROUP, "url", r"https?://\S+"),
    (TokenCategory.GROUP, "path", r"(?:[A-Za-z]:)?[\\/][^\s\"']+"),
    (TokenCategory.INDEPENDENT, "email", r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
]

# Unicode punctuation classes forbidden in model-authored visible text.
_BAD_QUOTES = "«»‹›“”‘’′″"
_BAD_DASHES = "—–―−‐‑﹣"
_BAD_ANGLE_QUOTES = ("<<", ">>")

# Common PT-BR words whose ASCII-fied form is a spelling error. Only
# words that own a diacritic belong here: 'guerra' and friends never
# carry one and would flag correct text.
_ASCIIFIED = re.compile(
    r"\b(?:nao|voce|lider|configuracao|traducao|publicacao|informacao|"
    r"educacao|opcao|acao|estrategia|agencia|experiencia)\b",
    re.IGNORECASE,
)

_OPEN_TAG = re.compile(r"<([a-zA-Z][a-zA-Z0-9]*)>")
_CLOSE_TAG = re.compile(r"</([a-zA-Z][a-zA-Z0-9]*)>")


@dataclass
class ProtectionResult:
    protected: str
    tokens: list[ProtectedToken]
    map_by_token: dict[str, ProtectedToken]


class ProtectionError(ValueError):
    """A structural or token-contract violation in model output."""


def protect(text: str) -> ProtectionResult:
    """Replace technical fragments with opaque __KEEP_N__ tokens.

    The multiset of returned tokens is the contract the model must match.
    """
    tokens: list[ProtectedToken] = []
    by_token: dict[str, ProtectedToken] = {}

    def repl(match: re.Match) -> str:
        raw = match.group(0)
        category, group_name = _category_for(raw)
        n = len(tokens)
        token = f"__KEEP_{n}__"
        tok = ProtectedToken(token=token, raw=raw, category=category, group=group_name)
        tokens.append(tok)
        by_token[token] = tok
        return token

    out = text
    combined = re.compile(
        "|".join(f"({pattern})" for _cat, _group, pattern in _PATTERNS)
    )
    out = combined.sub(repl, out)
    return ProtectionResult(protected=out, tokens=tokens, map_by_token=by_token)


def _category_for(raw: str) -> tuple[TokenCategory, str | None]:
    for category, group_name, pattern in _PATTERNS:
        if re.fullmatch(pattern, raw):
            return category, group_name
    return TokenCategory.INDEPENDENT, None


def _token_number(tok: ProtectedToken) -> int:
    return int(tok.token.split("_")[3])


def validate_tokens(
    expected: list[ProtectedToken],
    actual_text: str,
    by_token: dict[str, ProtectedToken] | None = None,
) -> None:
    """Validate model output against the expected token contract.

    Raises ProtectionError on any violation; returns None when valid.
    """
    del by_token
    actual_nums = Counter(int(n) for n in TOKEN_RE.findall(actual_text))
    expected_nums = Counter(_token_number(t) for t in expected)
    if actual_nums != expected_nums:
        missing = expected_nums - actual_nums
        extra = actual_nums - expected_nums
        problems = []
        if missing:
            problems.append(f"missing tokens: {sorted(missing.elements())}")
        if extra:
            problems.append(f"extra or duplicated tokens: {sorted(extra.elements())}")
        raise ProtectionError("; ".join(problems))

    # Paired tags must be balanced and properly nested in output order.
    tag_names = {t.group for t in expected if t.category is TokenCategory.PAIRED and t.group}
    for name in sorted(tag_names):
        _check_tag_balance_and_nesting(actual_text, name)

    # Group tokens keep their relative order per group.
    groups: dict[str, list[str]] = {}
    for t in expected:
        if t.category is TokenCategory.GROUP and t.group:
            groups.setdefault(t.group, []).append(t.token)
    for group, tokens in groups.items():
        positions = [actual_text.find(t) for t in tokens]
        if any(p < 0 for p in positions):
            raise ProtectionError(f"group token(s) of {group!r} missing from the output")
        if positions != sorted(positions):
            raise ProtectionError(f"group tokens of {group!r} changed their relative order")

    for bad in _BAD_QUOTES + _BAD_DASHES:
        if bad in actual_text:
            raise ProtectionError(f"forbidden punctuation character {bad!r} present")
    for pair in _BAD_ANGLE_QUOTES:
        if pair in actual_text:
            raise ProtectionError(f"forbidden dialogue quotation {pair!r} present")


def _check_tag_balance_and_nesting(text: str, name: str) -> None:
    """Tags of one name must balance and never close before opening."""
    open_re = re.compile(rf"<{name}>")
    close_re = re.compile(rf"</{name}>")
    depth = 0
    pos = 0
    while pos < len(text):
        o = open_re.search(text, pos)
        c = close_re.search(text, pos)
        if o is None and c is None:
            break
        if o is None:
            depth -= 1
            if depth < 0:
                raise ProtectionError(f"tag </{name}> closes before its opening tag")
            pos = c.end() if c else len(text)
        elif c is None or o.start() < c.start():
            depth += 1
            pos = o.end()
        else:
            depth -= 1
            if depth < 0:
                raise ProtectionError(f"tag </{name}> closes before its opening tag")
            pos = c.end()
    if depth != 0:
        raise ProtectionError(f"tag <{name}> is unbalanced in the output")


def validate_punctuation(text: str) -> list[str]:
    """ASCII-safe punctuation + PT-BR orthography gate.

    Returns a list of violations (empty when clean). Accepts Unicode
    letters and diacritics; rejects smart quotes, guillemets, em/en/hbar
    dashes and dialogue angle brackets in model-authored text.
    """
    violations: list[str] = []
    if _ASCIIFIED.search(text):
        violations.append("ASCII-fied Portuguese word detected (missing diacritics)")
    return violations


# Token patterns for hand-written targets: only genuine technical
# fragments (placeholders, markup, bracket references, numbers, URLs).
# Deliberately lighter than protect(): prose like 'retire/die' or a
# leading '\n' escape is translatable text, not a path.
_MANUAL_TOKEN_RE = re.compile(
    r"\{[^{}]*\}"                       # {[PARAM1]}, {0}, {*refs*}
    r"|</?[a-zA-Z][a-zA-Z0-9]*(?:=[^<>]*)?/?>"   # <color=#...>, </color>
    r"|\[[^\[\]]*\]"                    # [myTitle], [i2p_One]
    r"|__KEEP_\d+__"
    r"|\d+(?:[.,]\d+)*%?"
    r"|https?://\S+"
)

_ESCAPE_RUN_RE = re.compile(r"\\+n")


def _manual_tokens(text: str) -> Counter:
    return Counter(_MANUAL_TOKEN_RE.findall(text))


def _align_escape_runs(en: str, pt: str) -> str:
    """Copy the EN escaped-newline structure onto the PT text.

    The game may treat literal '\n' escapes specially, so the PT keeps
    the exact same run lengths as the EN source.
    """
    en_runs = _ESCAPE_RUN_RE.findall(en)
    if not en_runs:
        return pt
    # Walk the PT runs with finditer and substitute each with the EN
    # counterpart so escape lengths match the source exactly.
    out = []
    last = 0
    for pos, match in enumerate(_ESCAPE_RUN_RE.finditer(pt)):
        out.append(pt[last:match.start()])
        out.append(en_runs[pos] if pos < len(en_runs) else match.group(0))
        last = match.end()
    out.append(pt[last:])
    return "".join(out)


def _balanced_markup(target: str) -> None:
    """Paired tags (with or without attributes) must balance and nest."""
    for name in sorted(set(re.findall(r"<([a-zA-Z][a-zA-Z0-9]*)", target))):
        token_re = re.compile(rf"<(/?){name}(?:[\s=][^<>]*)?>")
        depth = 0
        has_close = False
        for match in token_re.finditer(target):
            if match.group(1):
                depth -= 1
                has_close = True
                if depth < 0:
                    raise ProtectionError(f"tag </{name}> closes before its opening tag")
            else:
                depth += 1
        if has_close and depth != 0:
            raise ProtectionError(f"tag <{name}> is unbalanced in the output")


def validate_manual_target(source: str, target: str) -> str:
    """Validate a hand-written target against its source's token contract.

    Both sides are tokenized with the same technical-fragment patterns
    and the multisets must match exactly (nothing lost, nothing
    invented, numbers unchanged). Color tags must stay balanced.

    Returns the canonical target with escape runs mirrored to the
    source; callers must store the returned value.
    """
    target = _align_escape_runs(source, target)

    expected = _manual_tokens(source)
    actual = _manual_tokens(target)
    if expected != actual:
        missing = expected - actual
        extra = actual - expected
        problems = []
        if missing:
            problems.append(f"missing fragments: {sorted(missing.elements())[:4]}")
        if extra:
            problems.append(f"unexpected fragments: {sorted(extra.elements())[:4]}")
        raise ProtectionError("; ".join(problems))

    # Paired markup must stay balanced in the target.
    _balanced_markup(target)

    violations = validate_punctuation(target)
    if violations:
        raise ProtectionError("; ".join(violations))

    return target


def restore(text: str, by_token: dict[str, ProtectedToken]) -> str:
    """Restore known tokens from the unit's immutable map."""
    for token, tok in sorted(by_token.items(), key=lambda kv: -len(kv[0])):
        text = text.replace(token, tok.raw)
    if re.search(r"__KEEP_\d+__", text):
        raise ProtectionError("unknown tokens remained after restore")
    return text