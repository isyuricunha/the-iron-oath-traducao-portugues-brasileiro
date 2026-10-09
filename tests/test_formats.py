"""Format handler tests: HJSON round-trip and TextAsset XML helpers."""

from __future__ import annotations

import hjson
import tempfile
import unittest
from pathlib import Path

from localization.formats.hjson_format import HjsonFormat
from localization.formats.textasset_xml_format import TextAssetXmlFormat
from localization.pipeline import _guard_rebuild_validity

HJSON_SAMPLE = (
    "{\r\n"
    "\tUniqueId: ghoul_001,\r\n"
    "\tName: \"Grave Ghoul\",\r\n"
    "\tDescription: \"Someone who is responsible for all matters of the crypt.\",\r\n"
    "\tDamage: 15,\r\n"
    "\tTags: [\"Undead\", \"Fast\"],\r\n"
    "\tIconPath: sprites/ghoul,\r\n"
    "\tSpecial: \"Spawns every {0} days\",\r\n"
    "},\r\n"
    "{\r\n"
    "    Name: 'Void Spawn',\r\n"
    "\tDescription: \"It crawls from the dark.\",\r\n"
    "}"
)


class HjsonFormatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        base = self.root / "The Iron Oath_Data/StreamingAssets/Data/Enemies"
        base.mkdir(parents=True)
        self.sample = base / "ghoul.json"
        self.sample.write_bytes(HJSON_SAMPLE.encode("utf-8"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_discovery(self):
        paths = HjsonFormat.discover(self.root)
        assert len(paths) == 1
        assert paths[0].endswith("Enemies/ghoul.json")

    def test_extract_units_and_skip_rules(self):
        units, spans = HjsonFormat.extract(self.root, self.sample.relative_to(self.root).as_posix())
        ids = {u.unit_id: u.source for u in units}
        # Translatable prose kept.
        assert "Grave Ghoul" in ids.values()
        assert any("Someone who is responsible" in v for v in ids.values())
        assert "It crawls from the dark." in ids.values()
        # Numbers, technical ids and paths skipped.
        for value in ids.values():
            assert "15" != value.strip()
            assert "sprites/ghoul" not in value
            assert "ghoul_001" not in value
        # Placeholder-bearing strings ARE translatable (protected later).
        assert any("Spawns every {0} days" in v for v in ids.values())
        assert len(units) == len(spans)

    def test_roundtrip_byte_identical(self):
        rel = self.sample.relative_to(self.root).as_posix()
        rebuilt = HjsonFormat.rebuild(self.root, rel, {})
        assert rebuilt == self.sample.read_bytes()

    def test_duplicate_array_values_get_distinct_spans(self):
        base = self.root / "The Iron Oath_Data/StreamingAssets/Data/Dungeons/Encounters"
        base.mkdir(parents=True)
        sample = base / "tutorial.json"
        sample.write_bytes(
            b'{\r\n\tName: "Tutorial",\r\n\tGroup: ["Iron Flayer", "Nullbeast Prime", "Iron Flayer"],\r\n}\r\n'
        )
        rel = sample.relative_to(self.root).as_posix()
        units, spans = HjsonFormat.extract(self.root, rel)
        id0 = next(u.unit_id for u in units if u.source == "Iron Flayer" and u.unit_id.endswith("Group[0]"))
        id2 = next(u.unit_id for u in units if u.source == "Iron Flayer" and u.unit_id.endswith("Group[2]"))
        assert spans[id0] != spans[id2]
        rebuilt = HjsonFormat.rebuild(
            self.root, rel, {id0: "Esfolador de Ferro", id2: "Esfolador de Ferro"}
        ).decode("utf-8")
        assert rebuilt.count('"Esfolador de Ferro"') == 2
        assert "Iron Flayer" not in rebuilt
        hjson.loads(rebuilt)

    def test_guard_blocks_invalid_rebuild(self):
        strict = self.root / "The Iron Oath_Data/StreamingAssets/Data/Strict"
        strict.mkdir(parents=True)
        sample = strict / "clean.json"
        sample.write_bytes(b'{\r\n\tName: "Tutorial",\r\n}\r\n')
        rel = sample.relative_to(self.root).as_posix()
        with self.assertRaises(ValueError):
            _guard_rebuild_validity(self.root, rel, b'{\r\n\tName: "Tutorial"\r\n')
        # A still-valid rebuild passes the guard silently.
        _guard_rebuild_validity(self.root, rel, sample.read_bytes())

    def test_combat_groupings_are_reference_only(self):
        base = self.root / "The Iron Oath_Data/StreamingAssets/Data/Dungeons/CombatGroupings"
        base.mkdir(parents=True)
        sample = base / "bandits.json"
        sample.write_bytes(b'{\r\n\tGroup: ["Bloodhound"],\r\n\tLoot: ["Enchantment,any"],\r\n}\r\n')
        rel = sample.relative_to(self.root).as_posix()
        units, spans = HjsonFormat.extract(self.root, rel)
        assert units == [] and spans == {}
        assert HjsonFormat.rebuild(self.root, rel, {"x": "y"}) == sample.read_bytes()

    def test_single_token_values_are_identifiers(self):
        base = self.root / "The Iron Oath_Data/StreamingAssets/Data/Enemies"
        base.mkdir(parents=True, exist_ok=True)
        sample = base / "dark.json"
        sample.write_bytes(b'{\r\n\tResists: ["Dark"],\r\n\tElement: "Dark",\r\n\tTitle: "Dark Warden",\r\n}\r\n')
        rel = sample.relative_to(self.root).as_posix()
        units, _ = HjsonFormat.extract(self.root, rel)
        sources = {u.source for u in units}
        assert "Dark" not in sources
        assert "Dark Warden" in sources

    def test_rebuild_applies_only_translation_spans(self):
        rel = self.sample.relative_to(self.root).as_posix()
        units, _spans = HjsonFormat.extract(self.root, rel)
        target = next(u for u in units if u.source == "Grave Ghoul")
        translations = {target.unit_id: "Carniçal da Cova"}
        rebuilt = HjsonFormat.rebuild(self.root, rel, translations)
        text = rebuilt.decode("utf-8")
        assert 'Name: "Carniçal da Cova"' in text
        assert "Someone who is responsible" in text
        assert "\r\n" in text and "\t" in text
        # Re-parse the rebuilt file and confirm the applied value.
        with tempfile.TemporaryDirectory() as tmp2:
            root2 = Path(tmp2) / "The Iron Oath_Data/StreamingAssets/Data/Enemies"
            root2.mkdir(parents=True)
            (root2 / "ghoul.json").write_bytes(rebuilt)
            units2, _ = HjsonFormat.extract(Path(tmp2), rel)
            sources2 = {u.source for u in units2}
            assert "Carniçal da Cova" in sources2
            assert len(units2) == len(units)

    def test_single_quote_style_preserved(self):
        rel = self.sample.relative_to(self.root).as_posix()
        units, _ = HjsonFormat.extract(self.root, rel)
        target = next(u for u in units if u.source == "Void Spawn")
        rebuilt = HjsonFormat.rebuild(self.root, rel, {target.unit_id: "Prole do Vazio"})
        assert b"'Prole do Vazio'" in rebuilt


class TextAssetXmlTests(unittest.TestCase):
    XML = (
        '<?xml version="1.0" encoding="utf-16"?>\r\n'
        "<Localization>\r\n"
        "  <Section>\r\n"
        '    <TextKey name="lblDoor">\r\n'
        "      <EN>This is a label for a Door</EN>\r\n"
        "      <DE>Dies ist die Beschriftung einer Tür</DE>\r\n"
        "    </TextKey>\r\n"
        '    <TextKey name="lblExit">\r\n'
        "      <EN>Leave the building</EN>\r\n"
        "    </TextKey>\r\n"
        "  </Section>\r\n"
        "</Localization>"
    )

    def test_span_regex_finds_en_values(self):
        expected = {
            "textasset:assets:LocalizationText:lblDoor": "This is a label for a Door",
            "textasset:assets:LocalizationText:lblExit": "Leave the building",
        }
        mismatches = TextAssetXmlFormat.validate_str(self.XML, {})
        assert mismatches == 0
        fake_xml = self.XML.replace(
            "This is a label for a Door", "Rótulo da porta", 1
        )
        mismatches = TextAssetXmlFormat.validate_str(
            fake_xml, {"textasset:assets:LocalizationText:lblDoor": "Rótulo da porta"}
        )
        assert mismatches == 0
        mismatches = TextAssetXmlFormat.validate_str(
            fake_xml, {"textasset:assets:LocalizationText:lblDoor": "Outra coisa"}
        )
        assert mismatches == 1
        del expected

    def test_de_value_preserved(self):
        assert "Dies ist die Beschriftung einer Tür" in self.XML


if __name__ == "__main__":
    unittest.main()