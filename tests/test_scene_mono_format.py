"""Unit tests for the scene/prefab display-text scanner and patcher."""

import struct
import unittest

from localization.formats.scene_mono_format import (
    TMP_TEXT_SCRIPT,
    _spans_of_object,
    apply_edits,
)

TMP_SCRIPT = TMP_TEXT_SCRIPT


def _string(data: bytes) -> bytes:
    return struct.pack("<i", len(data)) + data + b"\x00" * ((-len(data)) % 4)


def build_tmp_raw(m_text: str, tail: bytes = b"\xde\xad\xbe\xef") -> bytes:
    """A minimal MonoBehaviour whose script is the TMP text component."""
    name = b"Label"
    return (
        struct.pack("<iq", 7, 123)  # m_GameObject PPtr
        + b"\x01\x00\x00\x00"  # m_Enabled
        + struct.pack("<iq", *TMP_SCRIPT)  # m_Script PPtr
        + _string(name)
        + b"\x00" * 40  # fixed block up to m_Text
        + _string(m_text.encode("utf-8"))
        + tail
    )


class SceneMonoFormatTest(unittest.TestCase):
    def test_spans_find_m_text_at_tmp_site(self):
        raw = build_tmp_raw("Continue Campaign")
        spans = _spans_of_object(raw, 42)
        self.assertEqual([s.text for s in spans], ["Continue Campaign"])
        self.assertEqual(spans[0].path_id, 42)

    def test_non_tmp_scripts_are_ignored(self):
        raw = bytearray(build_tmp_raw("Continue Campaign"))
        raw[0x10:0x1C] = struct.pack("<iq", 1, 999)  # some other script
        self.assertEqual(_spans_of_object(bytes(raw), 1), [])

    def test_apply_edits_longer_value(self):
        raw = build_tmp_raw("Quit")
        spans = _spans_of_object(raw, 1)
        span = spans[0]
        edits = [(span.prefix_offset, (span.content_end + 3) & ~3, span.text)]
        patched = apply_edits(raw, edits, {"Quit": "Sair"})
        reparsed = _spans_of_object(patched, 1)
        self.assertEqual([s.text for s in reparsed], ["Sair"])
        self.assertIn(b"\xde\xad\xbe\xef", patched)

    def test_apply_edits_multiple_strings(self):
        raw = build_tmp_raw("Settings")
        spans = _spans_of_object(raw, 1)
        span = spans[0]
        edits = [(span.prefix_offset, (span.content_end + 3) & ~3, span.text)]
        patched = apply_edits(raw, edits, {"Settings": "Opções"})
        self.assertEqual([s.text for s in _spans_of_object(patched, 1)], ["Opções"])

    def test_rich_text_survives(self):
        text = '<color=#00aadd>Programmer</color>\nChris Wingard'
        raw = build_tmp_raw(text)
        spans = _spans_of_object(raw, 1)
        span = spans[0]
        edits = [(span.prefix_offset, (span.content_end + 3) & ~3, span.text)]
        patched = apply_edits(
            raw,
            edits,
            {text: "<color=#00aadd>Programador</color>\nChris Wingard"},
        )
        self.assertEqual(
            [s.text for s in _spans_of_object(patched, 1)],
            ["<color=#00aadd>Programador</color>\nChris Wingard"],
        )


if __name__ == "__main__":
    unittest.main()
