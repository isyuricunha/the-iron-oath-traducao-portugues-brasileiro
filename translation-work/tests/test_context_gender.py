from __future__ import annotations

from pathlib import Path
import sys
import unittest

TW = Path(__file__).resolve().parent.parent
if str(TW) not in sys.path:
    sys.path.insert(0, str(TW))

from context import (
    CONFIRMED_CAMPAIGN_ACTORS,
    analyze_text_context,
    enrich_records_with_speaker_clues,
    parse_createnpc_chunk,
)


class ContextGenderTests(unittest.TestCase):
    def test_a_speaker_and_script_table_cluelinking(self) -> None:
        records = [
            {
                "id": "i2:Speaker_Jessica",
                "key": "Speaker_Jessica",
                "source_file": "The Iron Oath_Data/resources.assets",
                "EN": "Jessica",
                "PTBR": "",
            },
            {
                "id": "dlg:1",
                "table": "ScriptJessica",
                "key": "Jessica_001",
                "source_file": "script.json",
                "EN": "Follow my lead to the gate.",
                "PTBR": "",
            },
        ]
        enriched = enrich_records_with_speaker_clues(records)
        line_ctx = enriched[1]["context"]
        self.assertEqual(line_ctx["speaker_id"], "Speaker_Jessica")
        self.assertEqual(line_ctx["speaker_label"], "Jessica")
        self.assertIn("ScriptJessica", line_ctx["speaker_evidence"])
        self.assertIn("review_speaker_gender_before_agreement", line_ctx["review_flags"])
        self.assertEqual(line_ctx["certainty"], "suggestive_identifier")
        # Name-only clue never infers male/female speaker_gender
        self.assertEqual(line_ctx.get("speaker_gender", "unknown"), "unknown")

    def test_b_unknown_and_ambiguous_names_remain_unknown(self) -> None:
        npcs = parse_createnpc_chunk(
            ['<<CreateNpc "id:priestNpc,img:OldPriest,name:Ada">>']
        )
        ctx = analyze_text_context(
            "{npc:priestNpc}Take these supplies.",
            chunk_npcs=npcs,
        )
        self.assertEqual(ctx["speaker_gender"], "unknown")
        self.assertEqual(ctx["certainty"], "unknown")
        self.assertIn("ambiguous_speaker_gender", ctx["review_flags"])

        # Substring traps ('hjson', 'human', 'shaman', 'command') must NOT trigger male/son/man
        for trap_kind in ("hjson", "shaman_guide", "command_unit", "human_merchant"):
            c = analyze_text_context("Ready for the march.", kind=trap_kind)
            self.assertNotEqual(c.get("speaker_gender"), "male", msg=f"False positive male on {trap_kind!r}")

    def test_c_first_person_female_speaker_describing_male_referent(self) -> None:
        ctx = analyze_text_context(
            "I tracked him through the mire; he walked straight into my trap.",
            kind="Huntress",
        )
        self.assertEqual(ctx["speaker_gender"], "female")
        self.assertEqual(ctx["grammatical_person"], "first_person_speaker")
        self.assertEqual(ctx["referent_pronoun_clue"], "male")
        self.assertIn("speaker_differs_from_male_referent", ctx["review_flags"])

    def test_d_dynamic_runtime_entity_and_explicit_sex(self) -> None:
        ctx_player = analyze_text_context("{player:0}My title is [myTitle] and [pronounSingular] will lead.")
        self.assertEqual(ctx_player["speaker_gender"], "dynamic_player")
        self.assertEqual(ctx_player["certainty"], "dynamic")
        self.assertIn("dynamic_player_gender", ctx_player["review_flags"])
        self.assertIn("dynamic_runtime_entity", ctx_player["review_flags"])

        ctx_sex = analyze_text_context("[fullName] asks for support.", explicit_sex="f")
        self.assertEqual(ctx_sex["referent_gender"], "female")
        self.assertEqual(ctx_sex["certainty"], "confirmed")

        # Confirmed campaign actor remains confirmed
        ctx_euric = analyze_text_context("{mainChar0}We hold the bridge.")
        self.assertEqual(ctx_euric["speaker"], CONFIRMED_CAMPAIGN_ACTORS["mainChar0"]["name"])
        self.assertEqual(ctx_euric["speaker_gender"], "male")
        self.assertEqual(ctx_euric["certainty"], "confirmed")

    def test_e_mixed_referents_and_neighbor_clues(self) -> None:
        ctx_mixed = analyze_text_context("She warned him at the tavern.")
        self.assertEqual(ctx_mixed["referent_pronoun_clue"], "mixed_male_and_female")
        self.assertIn("mixed_third_person_referents", ctx_mixed["review_flags"])

        ctx_nb = analyze_text_context(
            "Stay silent.",
            neighbor_lines=["He dropped the torch and ran."],
        )
        self.assertIn("male", ctx_nb["neighbor_pronoun_hint"])


if __name__ == "__main__":
    unittest.main()
