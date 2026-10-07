"""Format handler registry.

Each handler owns discovery, parsing, stable unit ids, extraction,
reconstruction, complete-file validation, encoding preservation and atomic
output for one file format. Handlers fail loudly when lossless
reconstruction cannot be guaranteed.
"""

from __future__ import annotations

from .hjson_format import HjsonFormat
from .textasset_xml_format import TextAssetXmlFormat

HANDLERS = {
    "hjson": HjsonFormat,
    "textasset_xml": TextAssetXmlFormat,
}


def discover(input_root, fmt: str | None) -> dict[str, list[str]]:
    """Return {handler_name: [relative paths]} for the input root."""
    result: dict[str, list[str]] = {}
    if fmt in (None, "auto", "hjson"):
        paths = HjsonFormat.discover(input_root)
        if paths:
            result["hjson"] = paths
    if fmt in (None, "auto", "textasset_xml"):
        paths = TextAssetXmlFormat.discover(input_root)
        if paths:
            result["textasset_xml"] = paths
    return result