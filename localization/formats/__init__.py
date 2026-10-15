"""Format handler registry.

Each handler owns discovery, parsing, stable unit ids, extraction,
reconstruction, complete-file validation, encoding preservation and atomic
output for one file format. Handlers fail loudly when lossless
reconstruction cannot be guaranteed.

Registration order matters for binary assets: textasset_xml writes the
patched resources.assets into staging first, then i2_mono patches the I2
table into that same staging copy.
"""

from __future__ import annotations

from .dll_format import DllFormat
from .hjson_format import HjsonFormat
from .i2_mono_format import I2MonoFormat
from .scene_mono_format import SceneMonoFormat
from .textasset_xml_format import TextAssetXmlFormat
from .yarn_format import YarnFormat

HANDLERS = {
    "hjson": HjsonFormat,
    "textasset_xml": TextAssetXmlFormat,
    "i2_mono": I2MonoFormat,
    "scene_mono": SceneMonoFormat,
    "yarn": YarnFormat,
    "dll": DllFormat,
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
    if fmt in (None, "auto", "i2_mono"):
        paths = I2MonoFormat.discover(input_root)
        if paths:
            result["i2_mono"] = paths
    if fmt in (None, "auto", "scene_mono"):
        paths = SceneMonoFormat.discover(input_root)
        if paths:
            result["scene_mono"] = paths
    if fmt in (None, "auto", "yarn"):
        paths = YarnFormat.discover(input_root)
        if paths:
            result["yarn"] = paths
    if fmt in (None, "auto", "dll"):
        paths = DllFormat.discover(input_root)
        if paths:
            result["dll"] = paths
    return result
