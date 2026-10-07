"""Game Profile: load, validate, fingerprint and compact runtime layers.

The audit record lives in game-profile.json at the repository root. The
runtime prompt receives only the compact stable core plus scoped entities,
never the evidence dump.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

DEFAULT_PROFILE_PATH = Path("game-profile.json")


class ProfileError(ValueError):
    pass


def load_profile(path: Path | None = None) -> dict:
    path = path or DEFAULT_PROFILE_PATH
    if not path.is_file():
        raise ProfileError(f"game profile not found at {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    validate(data)
    return data


def validate(data: dict) -> None:
    if data.get("schema_version") != 1:
        raise ProfileError("unsupported profile schema_version")
    if not isinstance(data.get("title"), str) or not data["title"]:
        raise ProfileError("profile requires a non-empty title")
    for section in ("identity", "setting", "tone", "text_domains", "style_rules"):
        if section not in data:
            raise ProfileError(f"profile is missing section {section!r}")
    if not isinstance(data.get("entities"), list):
        raise ProfileError("profile requires an entities list")
    if not isinstance(data.get("evidence"), dict) or not data["evidence"]:
        raise ProfileError("profile requires the evidence map")
    refs = set()
    for section in ("identity", "setting", "tone"):
        for ev in data.get(section, {}).get("ev", []):
            refs.add(ev)
    for entity in data["entities"]:
        for key in ("name", "type", "policy"):
            if key not in entity:
                raise ProfileError(f"entity {entity.get('name', '?')!r} is missing {key!r}")
    unknown = refs - set(data["evidence"])
    if unknown:
        raise ProfileError(f"profile references unknown evidence ids: {sorted(unknown)}")


def fingerprint(data: dict) -> str:
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def runtime_core(data: dict) -> str:
    """The stable compact layer sent with every translation prompt."""
    identity = data["identity"]
    setting = data["setting"]
    tone = data["tone"]
    entity_lines = []
    for entity in data["entities"]:
        target = f" -> {entity['target']}" if entity.get("target") else ""
        entity_lines.append(
            f"- {entity['name']} ({entity['type']}, {entity['policy']}){target}"
        )
    return "\n".join(
        [
            f"Game: {identity['titles'][0]}. Genre: {', '.join(identity['genre'])}.",
            f"Premise: {identity['premise']}.",
            f"Setting: {setting['world_summary']}",
            f"Tone: {tone['range']}",
            "Localization consequences:",
        ]
        + [f"  - {rule}" for rule in tone["localization_consequences"]]
        + ["Global style rules:"]
        + [f"  - {rule}" for rule in data["style_rules"]]
        + ["Scoped entities (preserve/localize per policy):"]
        + entity_lines
    )


def scoped_entities_for(data: dict, texts: list[str]) -> list[dict]:
    """Entity cards relevant to a batch by name overlap."""
    joined = " ".join(texts).lower()
    out = []
    for entity in data["entities"]:
        if entity["name"].lower() in joined:
            out.append(entity)
    return out[:12]