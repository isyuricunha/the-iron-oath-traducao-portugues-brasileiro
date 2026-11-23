"""Speaker, addressee, referent, and gender-disambiguation context analyzer."""
from __future__ import annotations

import re

# Known fixed campaign characters verified directly from TioQuests.yarn.txt prose.
CONFIRMED_CAMPAIGN_ACTORS: dict[str, dict] = {
    "mainChar0": {
        "name": "Euric",
        "gender": "male",
        "certainty": "confirmed",
        "evidence": "Yarn campaign scripts refer to mainChar0/[FNameC0] with 'he/his' and 'his nephew Gedrick'.",
    },
    "mainChar1": {
        "name": "Vahn",
        "gender": "male",
        "certainty": "confirmed",
        "evidence": "Yarn campaign scripts refer to mainChar1/[FNameC1] with 'he/his' and 'more death than any man should'.",
    },
    "advisor": {
        "name": "[FNameAdvisor]",
        "gender": "male",
        "certainty": "confirmed",
        "evidence": "Yarn campaign scripts refer to advisor/[FNameAdvisor] with 'He leans in' and 'his injured leg'.",
    },
    "mainAnt0": {
        "name": "Torin Bloodhand / [FNameA0]",
        "gender": "male",
        "certainty": "confirmed",
        "evidence": "Yarn campaign scripts refer to mainAnt0/[FNameA0] with 'he/his'.",
    },
    "Gedrick": {
        "name": "Gedrick",
        "gender": "male",
        "certainty": "confirmed",
        "evidence": "Yarn campaign scripts identify Gedrick as Euric's nephew ('the boy').",
    },
    "campaignCommanderOwaynNpc": {
        "name": "Commander Owayn",
        "gender": "male",
        "certainty": "confirmed",
        "evidence": "SE.CampaignFortVanguardEnterFortEventNew describes Owayn as 'an older man... He smiles'.",
    },
}

_CREATE_NPC_RE = re.compile(r'<<CreateNpc\s+"([^"]+)"\s*>>')
_SPEAKER_TAG_RE = re.compile(r"^\{(mainChar\d+|mainAnt\d+|advisor|player:[^}]+|npc:([^}]+))\}(.*)$", re.DOTALL)
_DYNAMIC_TOKEN_RE = re.compile(
    r"\{player:[^}]+\}|\[FNameP\d+\]|\[myTitle\]|\[pronoun(?:Sing|Obj|Poss|Singular|Objective|Possessive)[^\]]*\]|\[Pronoun[^\]]*\]|\[fullName\]",
    re.IGNORECASE,
)
_MALE_PRON_RE = re.compile(r"\b(?:he|him|his|himself)\b", re.IGNORECASE)
_FEMALE_PRON_RE = re.compile(r"\b(?:she|her|hers|herself)\b", re.IGNORECASE)
_FIRST_PERSON_RE = re.compile(r"\b(?:I|I'm|I've|I'll|I'd|my|mine|myself)\b")
_SECOND_PERSON_RE = re.compile(r"\b(?:you|your|yours|yourself|you're|you've|you'll)\b", re.IGNORECASE)


def parse_createnpc_chunk(chunk_lines: list[str]) -> dict[str, dict]:
    """Extract {npc_id: {name, img, gender_clue, certainty, evidence}} from a Yarn chunk."""
    npcs: dict[str, dict] = {}
    for line in chunk_lines:
        m = _CREATE_NPC_RE.search(line.strip())
        if not m:
            continue
        fields: dict[str, str] = {}
        for part in m.group(1).split(","):
            if ":" in part:
                k, v = part.split(":", 1)
                fields[k.strip()] = v.strip()
        npc_id = fields.get("id", "")
        if not npc_id:
            continue
        img = fields.get("img", "")
        name = fields.get("name", "")
        clue, cert, ev = _infer_sprite_clue(img, name)
        npcs[npc_id] = {
            "name": name,
            "img": img,
            "gender": clue,
            "certainty": cert,
            "evidence": ev,
        }
    return npcs


def _infer_sprite_clue(img: str, name: str) -> tuple[str, str, str]:
    low_img = img.lower()
    low_name = name.lower()
    if "female" in low_img or low_img == "woman" or "goddess" in low_name or low_name in ("woman", "mother"):
        return "female", "inferred_from_asset", f"CreateNpc img={img!r}, name={name!r}"
    if "male" in low_img or low_img == "man" or "father " in low_name or "king" in low_name or low_name in ("man", "old man"):
        return "male", "inferred_from_asset", f"CreateNpc img={img!r}, name={name!r}"
    # Name-only or ambiguous portrait (e.g. OldPriest used for 'Ada', Noble0, Bandit0) must stay unknown.
    return "unknown", "unknown", f"CreateNpc img={img!r}, name={name!r} (name/portrait alone is insufficient)"


_FEMALE_ROLE_TOKENS = frozenset({
    "huntress", "valkyrie", "goddess", "queen", "mother", "sister",
    "daughter", "lady", "female", "woman", "matriarch", "seductress",
    "temptress", "hag", "priestess", "empress",
})
_MALE_ROLE_TOKENS = frozenset({
    "king", "lord", "father", "brother", "son", "uncle", "nephew",
    "male", "man", "prince", "emperor",
})
_NON_SPEAKER_KINDS = frozenset({
    "narration", "choice", "hjson", "yarn_cmd", "i2_ui", "scene_ui",
    "dll_literal", "textasset_xml", "codex_name", "codex_description",
    "codex_ability", "codex_passive", "eventtitle", "eventdescription",
    "questdescription", "questintro", "shortdescription", "longdescription",
    "objective", "objectivetext", "tooltip", "text", "name", "description", "tip",
})


def _infer_label_gender(label: str) -> tuple[str, str, str]:
    """Evaluate Speaker_* / Script* / class identifiers conservatively without guessing from names alone."""
    words = re.findall(r"[a-z]+", label.lower())
    word_set = set(words)
    joined = " ".join(words)
    if word_set & _FEMALE_ROLE_TOKENS:
        return "female", "inferred_from_role", f"role/title keyword in identifier {label!r}"
    if (word_set & _MALE_ROLE_TOKENS) or "god of" in joined:
        return "male", "inferred_from_role", f"role/title keyword in identifier {label!r}"
    return "unknown", "unknown", f"identifier {label!r} provides identity only; name alone is not proof of gender"


def analyze_text_context(
    source: str,
    *,
    kind: str = "",
    node: str = "",
    table: str = "",
    key: str = "",
    chunk_npcs: dict[str, dict] | None = None,
    neighbor_lines: list[str] | None = None,
    explicit_sex: str | None = None,
    class_req: list[str] | None = None,
) -> dict:
    """Build read-only speaker/addressee/referent context metadata with explicit certainty."""
    ctx: dict = {}
    flags: list[str] = []
    chunk_npcs = chunk_npcs or {}
    neighbor_lines = neighbor_lines or []

    # 1. Explicit gender field (e.g., GuildCouncil Sex: "m")
    if explicit_sex:
        norm = {"m": "male", "f": "female"}.get(explicit_sex.lower(), "unknown")
        ctx["referent_gender"] = norm
        ctx["certainty"] = "confirmed" if norm != "unknown" else "unknown"
        ctx["evidence"] = f"explicit Sex={explicit_sex!r} field in source record"

    # 2. Yarn speaker prefix or explicit speaker identifier
    speaker_match = _SPEAKER_TAG_RE.match(source.strip())
    if speaker_match:
        tag = speaker_match.group(1)
        npc_id = speaker_match.group(2)
        if tag in CONFIRMED_CAMPAIGN_ACTORS:
            actor = CONFIRMED_CAMPAIGN_ACTORS[tag]
            ctx["speaker"] = actor["name"]
            ctx["speaker_gender"] = actor["gender"]
            ctx["certainty"] = actor["certainty"]
            ctx["evidence"] = actor["evidence"]
        elif tag.startswith("player:"):
            ctx["speaker"] = tag
            ctx["speaker_gender"] = "dynamic_player"
            ctx["certainty"] = "dynamic"
            ctx["evidence"] = "player-selected or recruited mercenary character at runtime"
            flags.append("dynamic_player_gender")
        elif npc_id:
            if npc_id in CONFIRMED_CAMPAIGN_ACTORS:
                actor = CONFIRMED_CAMPAIGN_ACTORS[npc_id]
                ctx["speaker"] = actor["name"]
                ctx["speaker_gender"] = actor["gender"]
                ctx["certainty"] = actor["certainty"]
                ctx["evidence"] = actor["evidence"]
            elif npc_id in chunk_npcs:
                info = chunk_npcs[npc_id]
                ctx["speaker"] = f"{info['name']} ({npc_id})" if info["name"] else npc_id
                ctx["speaker_gender"] = info["gender"]
                ctx["certainty"] = info["certainty"]
                ctx["evidence"] = info["evidence"]
                if info["gender"] == "unknown":
                    flags.append("ambiguous_speaker_gender")
            else:
                ctx["speaker"] = npc_id
                ctx["speaker_gender"] = "unknown"
                ctx["certainty"] = "unknown"
                ctx["evidence"] = f"Yarn speaker tag {{npc:{npc_id}}} without gender metadata"
                flags.append("ambiguous_speaker_gender")
    elif kind and kind.lower() not in _NON_SPEAKER_KINDS:
        # Speaker: line or Show* / table context
        ctx["speaker"] = kind
        g, cert, ev = _infer_label_gender(kind)
        ctx["speaker_gender"] = g
        ctx["certainty"] = ctx.get("certainty") or cert
        ctx["evidence"] = ctx.get("evidence") or ev

    # 3. Evaluate table / key clues (e.g. Speaker_Jessica, ScriptJessica) when present
    for label_kind, label_val in (("table", table), ("key", key)):
        if not label_val:
            continue
        if label_val.startswith(("Speaker_", "Script")):
            ctx[f"{label_kind}_identifier"] = label_val
            if "speaker" not in ctx:
                ctx["speaker"] = label_val
                ctx["speaker_gender"] = "unknown"
                ctx["certainty"] = "unknown"
                ctx["evidence"] = f"{label_kind}={label_val!r} names participant without explicit gender flag"
                flags.append("name_only_identifier")

    # 4. Class requirement hints (e.g., Huntress)
    if class_req:
        non_empty = [c for c in class_req if c and c != "None"]
        if non_empty:
            ctx["class_requirements"] = non_empty
            if any("Huntress" in c or "Valkyrie" in c for c in non_empty):
                ctx.setdefault("participant_clue", "female class requirement (Huntress/Valkyrie)")

    # 5. Dynamic runtime pronouns / placeholders inside the text itself
    dyn = _DYNAMIC_TOKEN_RE.findall(source)
    if dyn:
        ctx["dynamic_tokens"] = sorted(set(dyn))
        flags.append("dynamic_runtime_entity")

    # 6. Distinguish 1st-person speaker vs 3rd-person referent pronouns in the text (and immediate neighbors)
    has_1p = bool(_FIRST_PERSON_RE.search(source))
    has_2p = bool(_SECOND_PERSON_RE.search(source))
    male_refs = _MALE_PRON_RE.findall(source)
    female_refs = _FEMALE_PRON_RE.findall(source)
    if has_1p:
        ctx["grammatical_person"] = "first_person_speaker"
    if has_2p:
        ctx["addresses_player_or_listener"] = True
    if male_refs and female_refs:
        ctx["referent_pronoun_clue"] = "mixed_male_and_female"
        flags.append("mixed_third_person_referents")
    elif male_refs:
        ctx["referent_pronoun_clue"] = "male"
        # First-person speaker describing a male 3rd-person referent must NOT flip referent to speaker's gender.
        if ctx.get("speaker_gender") == "female":
            flags.append("speaker_differs_from_male_referent")
    elif female_refs:
        ctx["referent_pronoun_clue"] = "female"
        if ctx.get("speaker_gender") == "male":
            flags.append("speaker_differs_from_female_referent")
    elif neighbor_lines and "speaker_gender" not in ctx:
        joined = " ".join(neighbor_lines)
        nb_m = bool(_MALE_PRON_RE.search(joined))
        nb_f = bool(_FEMALE_PRON_RE.search(joined))
        if nb_m and not nb_f:
            ctx["neighbor_pronoun_hint"] = "male (neighboring dialogue; verify referent before agreement)"
        elif nb_f and not nb_m:
            ctx["neighbor_pronoun_hint"] = "female (neighboring dialogue; verify referent before agreement)"

    if flags:
        ctx["review_flags"] = sorted(set(flags))
    return ctx


def enrich_records_with_speaker_clues(records: list[dict]) -> list[dict]:
    """Cross-reference Speaker_* entries and Script* dialogue tables across a record batch.

    Links e.g. table 'ScriptJessica' or key prefix 'Jessica_' to 'Speaker_Jessica'
    when present, exposing grounded identity clues and their provenance/limitations
    without fabricating missing gender facts.
    """
    speaker_entries: dict[str, dict] = {}
    for rec in records:
        key = str(rec.get("key") or "")
        if key.startswith("Speaker_"):
            suffix = key[len("Speaker_"):]
            speaker_entries[suffix.lower()] = {
                "key": key,
                "label": rec.get("EN", ""),
                "source_file": rec.get("source_file", ""),
            }

    enriched: list[dict] = []
    for rec in records:
        out = dict(rec)
        ctx = dict(out.get("context") or {})
        table = str(out.get("table") or ctx.get("table") or "")
        key = str(out.get("key") or ctx.get("key") or "")
        en = str(out.get("EN") or "")

        matched_speaker = None
        evidence_parts: list[str] = []
        if table.startswith("Script") and len(table) > 6:
            candidate = table[6:].lower()
            if candidate in speaker_entries:
                matched_speaker = speaker_entries[candidate]
                evidence_parts.append(
                    f"dialogue table {table!r} matches {matched_speaker['key']} ({matched_speaker['label']!r})"
                )
        if not matched_speaker:
            for suffix, info in speaker_entries.items():
                if key.lower().startswith(suffix + "_") or key == info["key"]:
                    matched_speaker = info
                    evidence_parts.append(f"key {key!r} matches {info['key']} ({info['label']!r})")
                    break

        if matched_speaker:
            ctx["speaker_id"] = matched_speaker["key"]
            ctx["speaker_label"] = matched_speaker["label"]
            ctx["speaker_evidence"] = "; ".join(evidence_parts) + "; display name alone does not confirm grammatical gender unless corroborated"
            ctx.setdefault("certainty", "suggestive_identifier")
            flags = list(ctx.get("review_flags") or [])
            if "review_speaker_gender_before_agreement" not in flags:
                flags.append("review_speaker_gender_before_agreement")
            ctx["review_flags"] = sorted(set(flags))

        # Combine with text-level referent & dynamic pronoun analysis
        text_ctx = analyze_text_context(en, table=table, key=key)
        for k, v in text_ctx.items():
            if k == "review_flags":
                merged_flags = list(ctx.get("review_flags") or []) + list(v)
                ctx["review_flags"] = sorted(set(merged_flags))
            elif k not in ctx:
                ctx[k] = v

        out["context"] = ctx
        enriched.append(out)
    return enriched
