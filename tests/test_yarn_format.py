"""Yarn format handler tests: discovery, extraction kinds, rebuild."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from localization.formats.yarn_format import YarnFormat, _translatable

YARN_SAMPLE = (
    "title: SE.Test\r\n"
    "tags: ScriptedEvent\r\n"
    "position: 10,20\r\n"
    "---\r\n"
    "// a dev comment\r\n"
    "<<set $EventTrigger to \"ON_DUNGEON_LOAD\">>\r\n"
    "<<set $DaysUntilRepeat to -1>>\r\n"
    "<<set $EventTitle to \"A Looming Darkness\">>\r\n"
    "<<set $ShortDescription to \"Find the stash. [FNameC0] must survive\">>\r\n"
    "**The smell of the sewer fills the air.**\r\n"
    "->Let's hurry and find the amulet.\r\n"
    "    <<stop>>\r\n"
    "Euric: Watch the archer!\r\n"
    "123\r\n"
    "===\r\n"
)


class YarnFormatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        base = self.root / "The Iron Oath_Data/StreamingAssets/Data/QuestData"
        base.mkdir(parents=True)
        self.sample = base / "Test.yarn.txt"
        self.sample.write_bytes(YARN_SAMPLE.encode("utf-8"))
        self.rel = self.sample.relative_to(self.root).as_posix()

    def tearDown(self):
        self.tmp.cleanup()

    def test_discovery(self):
        assert YarnFormat.discover(self.root) == [self.rel]

    def test_noop_rebuild_byte_identical(self):
        assert YarnFormat.rebuild(self.root, self.rel, {}) == self.sample.read_bytes()

    def test_extraction_kinds(self):
        units, spans = YarnFormat.extract(self.root, self.rel)
        by_id = {u.unit_id: u for u in units}
        # whitelisted set variables extracted with their values
        assert any(
            u.source == "A Looming Darkness" and u.context["kind"] == "EventTitle"
            for u in units
        )
        assert any(
            u.source == "Find the stash. [FNameC0] must survive"
            for u in units
        )
        # enum/reference command NOT extracted
        assert not any("ON_DUNGEON_LOAD" in u.source for u in units)
        # narration + choice + speaker extracted
        assert any(u.source == "The smell of the sewer fills the air." for u in units)
        assert any(u.source == "Let's hurry and find the amulet." for u in units)
        assert any(u.source == "Watch the archer!" for u in units)
        # pure number line refused
        assert not any(u.source == "123" for u in units)
        # every unit has a span
        assert all(uid in spans for uid in by_id)

    def test_rebuild_preserves_structure(self):
        units, _ = YarnFormat.extract(self.root, self.rel)
        translations = {}
        for u in units:
            translations[u.unit_id] = {
                "A Looming Darkness": "Uma Escuridao Iminente",
                "Find the stash. [FNameC0] must survive": "Ache o escondido. [FNameC0] deve sobreviver",
                "The smell of the sewer fills the air.": "O cheiro de esgoto enche o ar.",
                "Let's hurry and find the amulet.": "Vamos rapido achar o amuleto.",
                "Watch the archer!": "Cuidado com o arqueiro!",
            }.get(u.source, u.source)
        rebuilt = YarnFormat.rebuild(self.root, self.rel, translations).decode("utf-8")
        # structure intact
        assert rebuilt.startswith("title: SE.Test\r\n")
        assert '<<set $EventTrigger to "ON_DUNGEON_LOAD">>' in rebuilt
        assert "<<stop>>" in rebuilt
        assert rebuilt.count("===\r\n") == 1
        # display values translated, prefixes preserved
        assert '<<set $EventTitle to "Uma Escuridao Iminente">>' in rebuilt
        assert "<<set $ShortDescription to \"Ache o escondido. [FNameC0] deve sobreviver\">>" in rebuilt
        assert "**O cheiro de esgoto enche o ar.**" in rebuilt
        assert "->Vamos rapido achar o amuleto." in rebuilt
        assert "Euric: Cuidado com o arqueiro!" in rebuilt
        assert "123" in rebuilt

    def test_quotes_escaped_inside_set_values(self):
        units, _ = YarnFormat.extract(self.root, self.rel)
        target_id = next(u.unit_id for u in units if u.source == "A Looming Darkness")
        rebuilt = YarnFormat.rebuild(
            self.root, self.rel, {target_id: 'Diz "oi" pra ele'}
        ).decode("utf-8")
        assert '<<set $EventTitle to "Diz \"oi\" pra ele">' + ">" in rebuilt

    def test_translatable_refusals(self):
        assert _translatable("ON_DUNGEON_LOAD") is False
        assert _translatable("123") is False
        assert _translatable("...") is False
        assert _translatable("Watch the archer!") is True

    def test_show_command_args_span_exact(self):
        # Regression: the Show-branch span used to start one character
        # late, leaving a stray leading character in the published file
        # and compounding on every publication that reused the overlay.
        sample = (
            "title: SE.Tut\r\n"
            "---\r\n"
            '<<ShowTutorialMessage "Exploration Events" "When interacting you choose.>>\r\n'
            '<<ShowTutorialMessage "TutorialX" "Enchantments" "Scrolls apply. To use one, press <sprite name=\\"PC-leftClick\\"> now.>>\r\n'
            "===\r\n"
        )
        (self.root / self.rel).write_bytes(sample.encode("utf-8"))
        units, spans = YarnFormat.extract(self.root, self.rel)
        decoded = sample.encode("utf-8").decode("utf-8-sig")
        for u in units:
            start, end, _ = spans[u.unit_id]
            assert decoded[start:end] == u.source, (u.unit_id, decoded[start:end], u.source)

    def test_shortcut_choice_extracted_with_link_intact(self):
        sample = (
            "title: SE.Shortcut\r\n"
            "---\r\n"
            "[[I'd like more details first.|nodeIntro2]]\r\n"
            "[[nodeJumpOnly]]\r\n"
            "===\r\n"
        )
        (self.root / self.rel).write_bytes(sample.encode("utf-8"))
        units, _ = YarnFormat.extract(self.root, self.rel)
        assert any(u.source == "I'd like more details first." for u in units)
        # jump-only line is not a display unit and must not be extracted
        assert not any(u.source == "nodeJumpOnly" for u in units)
        assert not any(u.source == "[[nodeJumpOnly]]" for u in units)
        target_id = next(u.unit_id for u in units if u.source == "I'd like more details first.")
        rebuilt = YarnFormat.rebuild(self.root, self.rel, {target_id: "Quero mais detalhes primeiro."}).decode("utf-8")
        assert "[[Quero mais detalhes primeiro.|nodeIntro2]]" in rebuilt

    def test_rebuild_idempotent_with_shortcuts(self):
        sample = (
            "title: SE.Tut\r\n"
            "---\r\n"
            '<<ShowTutorialMessage "Exploration Events" "When interacting you choose.>>\r\n'
            "[[I'd like more details first.|nodeIntro2]]\r\n"
            "===\r\n"
        )
        (self.root / self.rel).write_bytes(sample.encode("utf-8"))
        units, _ = YarnFormat.extract(self.root, self.rel)
        translations = {
            u.unit_id: {"Exploration Events": "Eventos de Exploracao",
                        "When interacting you choose.": "Ao interagir voce escolhe.",
                        "I'd like more details first.": "Quero mais detalhes primeiro."}.get(u.source, u.source)
            for u in units
        }
        first = YarnFormat.rebuild(self.root, self.rel, translations)
        second = YarnFormat.rebuild(self.root, self.rel, translations)
        assert first == second
        assert b"EEEventos" not in first and b"EEventos" not in first

    def test_legacy_whole_line_targets_dropped_for_shortcuts(self):
        # Regression: pre-shortcut-support checkpoints stored the whole
        # "[[text|node]]" line as the unit target; injecting it into the
        # text-only span double-wrapped the choice.
        from translate_project import _drop_legacy_shortcut_targets

        sample = (
            "title: SE.Shortcut\r\n"
            "---\r\n"
            "[[End|nodeEnd]]\r\n"
            "---\r\n"
        )
        (self.root / self.rel).write_bytes(sample.encode("utf-8"))
        unit_id = f"yarn:{self.rel}:2"
        kept = _drop_legacy_shortcut_targets({unit_id: "Fim"}, self.root)
        assert kept == {unit_id: "Fim"}
        dropped = _drop_legacy_shortcut_targets({unit_id: "[[End|nodeEnd]]"}, self.root)
        assert dropped == {}


if __name__ == "__main__":
    unittest.main()
