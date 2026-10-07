"""Runtime prompt assembly for the FreeInference translator."""

from __future__ import annotations

import json

from .glossary import Glossary

PREAMBLE = """You are the runtime translator of a game localization toolkit.
Reasoning is controlled by the API: you never emit reasoning traces.
Your final message contains ONLY the contract JSON. No Markdown fences,
no prose, no commentary, no chat-template or channel markers.
"""

ORTHOGRAPHY = """ORTHOGRAPHY
Use standard Brazilian Portuguese orthography. Normal Portuguese letters
and diacritics are Unicode text and must be preserved and produced
whenever linguistically required. Missing required diacritics are spelling
errors; for example, "nao", "voce", "lider", and "configuracao" are
incorrect where the proper forms are "não", "você", "líder", and
"configuração".

TYPOGRAPHY
The ASCII restriction below applies ONLY to model-authored visible
quotation, apostrophe, and dash-like punctuation. It does not apply to
letters, diacritics, or other normal Brazilian Portuguese characters.

In model-authored visible localized text, permit only ASCII double quote
" and ASCII apostrophe ' as quotation characters; when a dash-like
separator is necessary, permit only ASCII hyphen-minus - (U+002D). Never
introduce guillemets, curly or smart quotes, primes, em dash, en dash,
horizontal bar, Unicode hyphen/minus variants, or << and >> as dialogue
quotation marks.
"""

NATURALNESS = """Write as if the text had originally been authored in
Brazilian Portuguese for a professionally localized game.
Preserve meaning, gameplay information, intent, tone, characterization,
humor, and formality.
Do not preserve English syntax or word order when it sounds translated.
Reconstruct sentences naturally when needed.
UI text must be concise. Dialogue must sound spoken. Narrative text may be
expressive but must remain clear.
Never add, omit, summarize, weaken, intensify, or invent meaning.
"""

TOKEN_CONTRACT = """TOKEN PROTECTION
Protected fragments appear as opaque tokens of the form __KEEP_N__.
Copy the exact token multiset before translating, draft the translation,
compare the final token multiset with the original, repair any missing,
duplicated, altered or invented token, and only then produce the JSON.

Rule per token category:
- independent (numbers, hotkeys, simple placeholders): may move as a whole
  for natural Portuguese grammar, but each appears exactly once.
- paired (markup open and close tags): must stay balanced and properly
  nested.
- group (URLs, paths, color tags): keep their relative order.

Token Context is READ-ONLY: a raw_value field is grammatical context only
(gender, number, agreement). You must NEVER replace the token with the raw
value: always return the original __KEEP_N__ token.
Never invent tokens. Never leak raw protected values.
"""

RESPONSE_CONTRACT = """RESPONSE
Return a single JSON object with exactly this shape, nothing else:

{
  "translations": {
    "<exact requested id>": "<translation>"
  }
}

- Every requested id appears exactly once; no extra ids.
- Values are non-empty strings.
- The word JSON and this example define the required envelope.
"""

REVIEW_CONTRACT = """RESPONSE (review)
Return a single JSON object with exactly this shape, nothing else:

{
  "reviews": [
    {"id": "<exact id>", "decision": "keep", "text": "<complete translation>", "issue_codes": []}
  ]
}

- Every requested id appears exactly once; no extra ids.
- keep requires text EXACTLY equal to the supplied current translation.
- revise requires the complete corrected translation.
- issue_codes is a list; unknown labels are fine.
- Protected tokens must validate exactly as in translation mode.
"""


def build_review_prompt(
    profile_core: str,
    glossary: Glossary | None,
    units: list[dict],
    retry_feedback: str | None = None,
    project_context: str | None = None,
) -> str:
    """Assemble the bounded runtime prompt for one review batch."""
    sections = [
        PREAMBLE,
        "## Persona",
        (
            "You are a senior Brazilian Portuguese game localizer "
            "reviewing an existing PT-BR translation. You keep what is "
            "natural, consistent and faithful; you revise what is not."
        ),
        "## Game profile (read-only data)",
        profile_core,
    ]
    if glossary is not None:
        entries = glossary.relevant([u["source"] for u in units])
        if entries:
            lines = []
            for entry in entries:
                target = entry.get("target") or "(pending)"
                lines.append(
                    f"[{entry['status']}] {entry['term']} -> {target}"
                    + (f" (scope: {entry['sense']})" if entry.get("sense") else "")
                )
            sections.append("## Glossary constraints (locked first; obey them)")
            sections.append("\n".join(lines))
    if project_context:
        sections.append("## Project context overrides (read-only data)")
        sections.append(project_context)
    sections += [
        "## Syntax and grammar",
        NATURALNESS,
        ORTHOGRAPHY,
        TOKEN_CONTRACT,
    ]
    sections.append("## Units to review (current translation is read-only)")
    sections.append(json.dumps(units, ensure_ascii=False))
    sections.append(REVIEW_CONTRACT)
    if retry_feedback:
        sections.append("## Feedback on a previous rejected response")
        sections.append(retry_feedback)
    return "\n\n".join(sections)


def build_translation_prompt(
    profile_core: str,
    glossary: Glossary | None,
    units: list[dict],
    retry_feedback: str | None = None,
    project_context: str | None = None,
) -> str:
    """Assemble the bounded runtime prompt for one batch of units."""
    sections = [
        PREAMBLE,
        "## Persona",
        (
            "You are a senior Brazilian Portuguese game localizer "
            "specializing in dark-fantasy strategy RPGs. You translate with "
            "the vocabulary and rhythm of this genre."
        ),
        "## Game profile (read-only data)",
        profile_core,
    ]
    if glossary is not None:
        entries = glossary.relevant([u["source"] for u in units])
        if entries:
            lines = []
            for entry in entries:
                target = entry.get("target") or "(pending)"
                lines.append(
                    f"[{entry['status']}] {entry['term']} -> {target}"
                    + (f" (scope: {entry['sense']})" if entry.get("sense") else "")
                )
            sections.append("## Glossary constraints (locked first; obey them)")
            sections.append("\n".join(lines))
    if project_context:
        sections.append("## Project context overrides (read-only data)")
        sections.append(project_context)
    sections += [
        "## Syntax and grammar",
        NATURALNESS,
        ORTHOGRAPHY,
        TOKEN_CONTRACT,
    ]
    sections.append("## Units to translate")
    sections.append(json.dumps(units, ensure_ascii=False))
    sections.append(RESPONSE_CONTRACT)
    if retry_feedback:
        sections.append("## Feedback on a previous rejected response")
        sections.append(retry_feedback)
    return "\n\n".join(sections)


def retry_feedback_for(reason: str) -> str:
    return (
        f"The previous response was rejected because: {reason}\n"
        "Return the exact contract JSON with every requested id corrected."
    )