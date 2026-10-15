"""Unit tests for the I2 Languages table parser and patcher."""

import struct
import unittest

from localization.formats.i2_mono_format import (
    I2ParseError,
    apply_translations,
    is_translatable,
    parse_table,
)

HEADER = (
    struct.pack("<iq", 0, 0)  # m_GameObject PPtr
    + b"\x01\x00\x00\x00"  # m_Enabled
    + struct.pack("<iq", 1, 532)  # m_Script PPtr
    + struct.pack("<i", 11)
    + b"I2Languages"  # m_Name (padded by caller)
    + b"\x00"  # name pad
)
PRE_REGION = struct.pack("<iii", 0, 0, 1)


def _string(value: str) -> bytes:
    data = value.encode("utf-8")
    return struct.pack("<i", len(data)) + data + b"\x00" * ((-len(data)) % 4)


def build_raw(terms, tail: bytes = b"\x01\x00\x00\x00") -> bytes:
    """terms: list of (term, description, [values], flags, [touch])."""
    out = bytearray(HEADER + PRE_REGION)
    out += struct.pack("<i", len(terms))
    for term, desc, values, flags, touch in terms:
        out += _string(term)
        out += _string(desc)
        out += struct.pack("<i", len(values))
        for value in values:
            out += _string(value)
        out += struct.pack("<i", len(flags)) + bytes(flags)
        out += b"\x00" * ((-len(flags)) % 4)
        out += struct.pack("<i", len(touch))
        for value in touch:
            out += _string(value)
    out += b"\x00" * 12
    out += struct.pack("<i", 1) + _string("English") + _string("en") + struct.pack("<i", 0)
    out += b"\x01\x00\x00\x00" + b"\x00\x00\x00\x00"
    out += _string("https://example.invalid/exec")
    out += _string("key") + _string("name") + _string("0")
    out += tail
    return bytes(out)


class I2MonoFormatTest(unittest.TestCase):
    def test_parse_roundtrip(self):
        raw = build_raw(
            [
                ("tio_A", "", ["Apple", "", ""], b"\x00\x00\x00", []),
                ("tio_B", "desc", ["Pear", "x", ""], b"\x01\x02\x03", []),
            ]
        )
        table = parse_table(raw)
        self.assertEqual([t.term for t in table.terms], ["tio_A", "tio_B"])
        self.assertEqual([v.text for v in table.terms[0].values], ["Apple", "", ""])
        self.assertEqual(table.terms[1].flags, b"\x01\x02\x03")
        self.assertEqual([l[0] for l in table.languages], ["English"])

    def test_identity_patch_is_byte_identical(self):
        raw = build_raw([("tio_A", "", ["Apple", "", ""], b"\x00\x00\x00", [])])
        table = parse_table(raw)
        patched = apply_translations(raw, table, {"tio_A": "Apple"})
        self.assertEqual(patched, raw)

    def test_longer_and_shorter_values(self):
        raw = build_raw(
            [
                ("tio_A", "", ["Go", "", ""], b"\x00\x00\x00", []),
                ("tio_B", "", ["Continuation of the story", "", ""], b"\x00\x00\x00", []),
            ]
        )
        table = parse_table(raw)
        patched = apply_translations(raw, table, {"tio_A": "Ir", "tio_B": "Continuação da história"})
        reparsed = parse_table(patched)
        self.assertEqual(reparsed.term_map()["tio_A"].values[0].text, "Ir")
        self.assertEqual(
            reparsed.term_map()["tio_B"].values[0].text, "Continuação da história"
        )
        self.assertEqual(reparsed.term_map()["tio_B"].values[1].text, "")
        self.assertEqual(reparsed.term_map()["tio_B"].description, "")
        # untouched structures survive: language list and flags
        self.assertEqual([l[0] for l in reparsed.languages], ["English"])
        self.assertEqual(reparsed.term_map()["tio_A"].flags, b"\x00\x00\x00")

    def test_unknown_term_raises(self):
        raw = build_raw([("tio_A", "", ["Apple", "", ""], b"\x00\x00\x00", [])])
        table = parse_table(raw)
        with self.assertRaises(I2ParseError):
            apply_translations(raw, table, {"tio_MISSING": "x"})

    def test_translatable_filter(self):
        self.assertTrue(is_translatable("Health"))
        self.assertFalse(is_translatable(""))
        self.assertFalse(is_translatable("{*tio_other*}"))


if __name__ == "__main__":
    unittest.main()
