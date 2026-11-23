"""Surgical span-preserving XML handler for The Iron Oath Codex (Codex/codex.xml).

Extracts player-visible Codex titles (<codexCategory name="..."> when not
overridden by traitId), <description> blocks, and enemy <ability>/<passive>
names and descriptions, while preserving IDs, traits, comments, XML entities,
and |NEXTPAGE| page markers untouched.
"""
from __future__ import annotations

import html
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from context import analyze_text_context

CODEX_PATH = "The Iron Oath_Data/StreamingAssets/Data/Codex/codex.xml"

_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_TAG_RE = re.compile(r"<(/?)([A-Za-z_][A-Za-z0-9_]*)\b([^>]*?)(/?)>", re.DOTALL)
_ATTR_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"([^"]*)"')


def _xml_escape_attr(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _xml_escape_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class CodexXmlFormat:
    CODEX_PATH = CODEX_PATH

    @classmethod
    def discover(cls, input_root: Path) -> list[str]:
        return [CODEX_PATH] if (input_root / CODEX_PATH).is_file() else []

    @classmethod
    def extract(cls, input_root: Path, relpath: str = CODEX_PATH) -> tuple[list[dict], dict]:
        full = input_root / relpath
        if not full.is_file():
            return [], {}
        decoded = full.read_bytes().decode("utf-8-sig")
        # Validate well-formed XML first.
        ET.fromstring(decoded)

        comment_spans = [(m.start(), m.end()) for m in _COMMENT_RE.finditer(decoded)]

        def in_comment(pos: int) -> bool:
            return any(s <= pos < e for s, e in comment_spans)

        records: list[dict] = []
        spans: dict[str, tuple[int, int, str]] = {}

        cat_stack: list[tuple[str, int]] = []
        cat_counters: dict[int, int] = {}
        ability_idx = 0
        passive_idx = 0
        desc_open_end: int | None = None
        current_entry_id = ""
        current_region = ""

        for m in _TAG_RE.finditer(decoded):
            if in_comment(m.start()):
                continue
            is_close = bool(m.group(1))
            tag = m.group(2)
            attrs_raw = m.group(3)
            self_close = bool(m.group(4))
            attrs_start = m.start(3)

            if tag == "codexCategory":
                if is_close:
                    if cat_stack:
                        cat_stack.pop()
                    current_entry_id = cat_stack[-1][0] if cat_stack else ""
                    continue
                attrs = {am.group(1): (am.group(2), attrs_start + am.start(2), attrs_start + am.end(2)) for am in _ATTR_RE.finditer(attrs_raw)}
                depth = len(cat_stack)
                seq = cat_counters.get(depth, 0)
                cat_counters[depth] = seq + 1
                entry_id = attrs["id"][0] if "id" in attrs else f"cat_{depth}_{seq}"
                if "region" in attrs:
                    current_region = attrs["region"][0]
                if not self_close:
                    cat_stack.append((entry_id, seq))
                current_entry_id = entry_id
                ability_idx = 0
                passive_idx = 0

                if "traitId" not in attrs and "name" in attrs:
                    raw_val, v_start, v_end = attrs["name"]
                    val = html.unescape(raw_val)
                    if val.strip():
                        uid = f"codex_xml:{relpath}:{entry_id}:name:{seq}"
                        spans[uid] = (v_start, v_end, "attr")
                        ctx = analyze_text_context(val, kind="codex_category", key=entry_id)
                        ctx["element"] = "codexCategory/@name"
                        if current_region:
                            ctx["region"] = current_region
                        records.append({
                            "id": uid, "row": len(records), "source_file": relpath,
                            "format": "codex_xml", "key": f"{entry_id}/@name", "context": ctx,
                            "EN": val, "PTBR": "",
                        })
            elif tag == "description":
                if not is_close and not self_close:
                    desc_open_end = m.end()
                elif is_close and desc_open_end is not None:
                    raw_inner = decoded[desc_open_end : m.start()]
                    # Preserve leading/trailing newline padding around description body cleanly.
                    lead = len(raw_inner) - len(raw_inner.lstrip(" \t\r\n"))
                    trail = len(raw_inner) - len(raw_inner.rstrip(" \t\r\n"))
                    inner_start = desc_open_end + lead
                    inner_end = m.start() - trail
                    if inner_end > inner_start:
                        val = html.unescape(decoded[inner_start:inner_end])
                        uid = f"codex_xml:{relpath}:{current_entry_id or 'root'}:description"
                        if uid in spans:
                            uid = f"{uid}:{len(records)}"
                        spans[uid] = (inner_start, inner_end, "text")
                        ctx = analyze_text_context(val, kind="codex_description", key=current_entry_id)
                        ctx["element"] = "description"
                        if current_region:
                            ctx["region"] = current_region
                        records.append({
                            "id": uid, "row": len(records), "source_file": relpath,
                            "format": "codex_xml", "key": f"{current_entry_id}/description", "context": ctx,
                            "EN": val, "PTBR": "",
                        })
                    desc_open_end = None
            elif tag in ("ability", "passive") and not is_close:
                idx = ability_idx if tag == "ability" else passive_idx
                if tag == "ability":
                    ability_idx += 1
                else:
                    passive_idx += 1
                attrs = {am.group(1): (am.group(2), attrs_start + am.start(2), attrs_start + am.end(2)) for am in _ATTR_RE.finditer(attrs_raw)}
                for attr_name in ("name", "description"):
                    if attr_name not in attrs:
                        continue
                    raw_val, v_start, v_end = attrs[attr_name]
                    val = html.unescape(raw_val)
                    if not val.strip():
                        continue
                    uid = f"codex_xml:{relpath}:{current_entry_id}:{tag}[{idx}]:{attr_name}"
                    spans[uid] = (v_start, v_end, "attr")
                    ctx = analyze_text_context(val, kind=f"codex_{tag}_{attr_name}", key=current_entry_id)
                    ctx["element"] = f"{tag}/@{attr_name}"
                    records.append({
                        "id": uid, "row": len(records), "source_file": relpath,
                        "format": "codex_xml", "key": f"{current_entry_id}/{tag}[{idx}]/@{attr_name}",
                        "context": ctx, "EN": val, "PTBR": "",
                    })
        return records, spans

    @classmethod
    def rebuild(cls, input_root: Path, relpath: str, translations_by_id: dict[str, str]) -> bytes:
        full = input_root / relpath
        raw = full.read_bytes()
        bom = raw.startswith(b"\xef\xbb\xbf")
        decoded = raw.decode("utf-8-sig")
        records, spans = cls.extract(input_root, relpath)
        edits: list[tuple[int, int, str]] = []
        for rec in records:
            uid = rec["id"]
            val = translations_by_id.get(uid)
            if val is None:
                continue
            start, end, mode = spans[uid]
            encoded_val = _xml_escape_attr(val) if mode == "attr" else _xml_escape_text(val)
            edits.append((start, end, encoded_val))
        for start, end, repl in sorted(edits, reverse=True):
            decoded = decoded[:start] + repl + decoded[end:]
        ET.fromstring(decoded)
        data = decoded.encode("utf-8")
        return (b"\xef\xbb\xbf" + data) if bom else data
