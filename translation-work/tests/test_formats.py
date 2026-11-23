from __future__ import annotations

import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest

TW = Path(__file__).resolve().parent.parent
ROOT = TW.parent
if str(TW) not in sys.path:
    sys.path.insert(0, str(TW))

from formats.codex_xml_format import CodexXmlFormat
from formats.dll_format import DllFormat, find_logic_usages
from formats.hjson_format import HjsonFormat
from formats.i2_mono_format import I2MonoFormat, apply_translations as i2_apply, parse_table as i2_parse
from formats.scene_mono_format import SceneMonoFormat, TMP_TEXT_SCRIPT, scan_asset
from formats.textasset_xml_format import TextAssetXmlFormat
from formats.unity_assets import (
    CLASS_ID_MONOBEHAVIOUR,
    CLASS_ID_TEXT_ASSET,
    build_minimal_assets,
    encode_text_asset,
    parse_serialized_file,
    read_text_asset,
    rebuild_serialized_file,
)
from formats.yarn_format import YarnFormat
import native


def _s_align4(val: str) -> bytes:
    b = val.encode("utf-8")
    return struct.pack("<i", len(b)) + b + b"\x00" * ((-len(b)) % 4)


def _synth_i2_raw() -> bytes:
    header = (
        struct.pack("<iq", 0, 0)
        + b"\x01\x00\x00\x00"
        + struct.pack("<iq", 1, 532)
        + _s_align4("I2Languages")
        + struct.pack("<3i", 0, 0, 1)
    )
    terms = bytearray(header)
    terms += struct.pack("<i", 1)
    terms += _s_align4("tio_Smoke") + _s_align4("") + struct.pack("<i", 3) + _s_align4("Smoke Term") + _s_align4("") + _s_align4("")
    terms += struct.pack("<i", 3) + b"\x00\x00\x00\x00"
    terms += struct.pack("<i", 0)
    terms += b"\x00" * 12
    terms += struct.pack("<i", 1) + _s_align4("English") + _s_align4("en") + struct.pack("<i", 0)
    terms += b"\x01\x00\x00\x00\x00\x00\x00\x00" + _s_align4("") + _s_align4("") + _s_align4("") + _s_align4("0")
    return bytes(terms)


def _synth_scene_raw(text: str) -> bytes:
    return (
        struct.pack("<iq", 7, 100)
        + b"\x01\x00\x00\x00"
        + struct.pack("<iq", *TMP_TEXT_SCRIPT)
        + _s_align4("SmokeLabel")
        + b"\x00" * 40
        + _s_align4(text)
    )


class FormatRoundtripTests(unittest.TestCase):
    def test_hjson_lossless_and_single_word_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rel_item = "The Iron Oath_Data/StreamingAssets/Data/Items/Loot/armor.json"
            raw_item = (
                b"// armor comment\r\n"
                b"[\r\n"
                b"  {\r\n"
                b'    UniqueId: "armor_mail",\r\n'
                b'    name: "Mail",\r\n'
                b'    description: "Sturdy armor.",\r\n'
                b"  }\r\n"
                b"]\r\n"
            )
            (root / rel_item).parent.mkdir(parents=True, exist_ok=True)
            (root / rel_item).write_bytes(raw_item)

            rel_job = "The Iron Oath_Data/StreamingAssets/Data/Systems/PlayerJobs.json"
            raw_job = b'[\r\n  {\r\n    Job: "Scout",\r\n    Name: "Tracker",\r\n  }\r\n]\r\n'
            (root / rel_job).parent.mkdir(parents=True, exist_ok=True)
            (root / rel_job).write_bytes(raw_job)

            recs_item, _ = HjsonFormat.extract(root, rel_item)
            self.assertEqual([r["EN"] for r in recs_item], ["Mail", "Sturdy armor."])
            self.assertEqual(HjsonFormat.rebuild(root, rel_item, {}), raw_item)

            rebuilt = HjsonFormat.rebuild(
                root,
                rel_item,
                {recs_item[0]["id"]: "Cota de Malha", recs_item[1]["id"]: "Armadura resistente."},
            ).decode("utf-8")
            self.assertIn("// armor comment\r\n", rebuilt)
            self.assertIn('UniqueId: "armor_mail"', rebuilt)
            self.assertIn('name: "Cota de Malha"', rebuilt)
            self.assertIn('description: "Armadura resistente."', rebuilt)

            recs_job, _ = HjsonFormat.extract(root, rel_job)
            self.assertEqual([r["EN"] for r in recs_job], ["Tracker"])

    def test_yarn_lossless_action_bracket_and_logic_exclusion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rel = "The Iron Oath_Data/StreamingAssets/Data/QuestData/Sample.yarn.txt"
            raw = (
                b"title: SE.SmokeNode\r\n"
                b"---\r\n"
                b"// designer comment\r\n"
                b"<<set $EventTitle to \"Roadside Ambush\">>\r\n"
                b"<<ChangeQuestTrackerDisplayString Quest.AtAnyCost>>\r\n"
                b"{mainChar0}Stay behind my shield!\r\n"
                b"->[Locked:keyOrTools]Pick the chest\r\n"
                b"->[Action:Continue]\r\n"
                b"[You search the ruins quietly.]\r\n"
                b"===\r\n"
            )
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_bytes(raw)

            recs, _ = YarnFormat.extract(root, rel)
            ens = [r["EN"] for r in recs]
            self.assertEqual(
                ens,
                [
                    "Roadside Ambush",
                    "{mainChar0}Stay behind my shield!",
                    "[Locked:keyOrTools]Pick the chest",
                    "Continue",
                    "You search the ruins quietly.",
                ],
            )
            self.assertEqual(YarnFormat.rebuild(root, rel, {}), raw)

            pt_map = {
                recs[0]["id"]: "Emboscada na Estrada",
                recs[1]["id"]: "{mainChar0}Fique atras do meu escudo!",
                recs[2]["id"]: "[Locked:keyOrTools]Arrombar o bau",
                recs[3]["id"]: "Continuar",
                recs[4]["id"]: "Voce vasculha as ruinas em silencio.",
            }
            rebuilt = YarnFormat.rebuild(root, rel, pt_map).decode("utf-8")
            self.assertIn("// designer comment\r\n", rebuilt)
            self.assertIn("<<ChangeQuestTrackerDisplayString Quest.AtAnyCost>>", rebuilt)
            self.assertIn("->[Locked:keyOrTools]Arrombar o bau", rebuilt)
            self.assertIn("->[Action:Continuar]", rebuilt)
            self.assertIn("[Voce vasculha as ruinas em silencio.]", rebuilt)

    def test_codex_xml_lossless_and_trait_skip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rel = CodexXmlFormat.CODEX_PATH
            raw = (
                b'<?xml version="1.0" encoding="utf-8"?>\n'
                b"<!-- keep comment -->\n"
                b"<CodexDoc>\n"
                b'  <codexCategory name="Journals" id="journals" template="Journal" Visible="true">\n'
                b"    <description>Page one|NEXTPAGE|Page two &amp; more</description>\n"
                b'    <codexCategory name="Burn" traitId="Trait.Burn">\n'
                b"      <description>Deals fire damage.</description>\n"
                b"    </codexCategory>\n"
                b'    <ability name="Shield Bash" description="Stuns the foe." />\n'
                b'    <passive name="Iron Skin" description="Reduces damage." />\n'
                b"  </codexCategory>\n"
                b"</CodexDoc>\n"
            )
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_bytes(raw)

            recs, _ = CodexXmlFormat.extract(root, rel)
            ens = [r["EN"] for r in recs]
            self.assertNotIn("Burn", ens)
            self.assertIn("Journals", ens)
            self.assertIn("Page one|NEXTPAGE|Page two & more", ens)
            self.assertIn("Deals fire damage.", ens)
            self.assertIn("Shield Bash", ens)
            self.assertIn("Iron Skin", ens)
            self.assertEqual(CodexXmlFormat.rebuild(root, rel, {}), raw)

            pt_map = {r["id"]: f"PT {r['EN']}" for r in recs}
            rebuilt = CodexXmlFormat.rebuild(root, rel, pt_map).decode("utf-8")
            self.assertIn("<!-- keep comment -->", rebuilt)
            self.assertIn('name="Burn" traitId="Trait.Burn"', rebuilt)
            self.assertIn('name="PT Journals"', rebuilt)
            self.assertIn("PT Page one|NEXTPAGE|Page two &amp; more", rebuilt)

    def test_unity_assets_textasset_i2_and_scene_roundtrip(self) -> None:
        xml_body = '<?xml version="1.0" encoding="utf-8"?><Localization><TextKey name="k"><EN>Hello</EN></TextKey></Localization>'
        ta_raw = encode_text_asset("LocalizationText", xml_body)
        i2_raw = _synth_i2_raw()
        sc_raw = _synth_scene_raw("Smoke Button")
        asset_bytes = build_minimal_assets(
            [
                (96, CLASS_ID_TEXT_ASSET, ta_raw),
                (1841, CLASS_ID_MONOBEHAVIOUR, i2_raw),
                (2000, CLASS_ID_MONOBEHAVIOUR, sc_raw),
            ]
        )
        parsed = parse_serialized_file(asset_bytes)
        self.assertEqual(len(parsed.objects), 3)
        name, text, _ = read_text_asset(parsed.objects[0].raw)
        self.assertEqual(name, "LocalizationText")
        self.assertIn("Hello", text)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rel_res = "The Iron Oath_Data/resources.assets"
            (root / rel_res).parent.mkdir(parents=True, exist_ok=True)
            (root / rel_res).write_bytes(asset_bytes)

            tx_recs, _ = TextAssetXmlFormat.extract(root, rel_res)
            self.assertEqual([r["EN"] for r in tx_recs], ["Hello"])
            rebuilt_xml = TextAssetXmlFormat.rebuild_xml(root, rel_res, {tx_recs[0]["id"]: "Ola Mundo"})
            tx_rebuilt_raw = TextAssetXmlFormat.patch_asset_bytes(asset_bytes, rebuilt_xml)
            parsed2 = parse_serialized_file(tx_rebuilt_raw)
            _, text2, _ = read_text_asset(parsed2.objects[0].raw)
            self.assertIn("Ola Mundo", text2)

            table = i2_parse(parsed2.objects[1].raw)
            patched_i2 = i2_apply(parsed2.objects[1].raw, table, {"tio_Smoke": "Termo Teste"})
            rebuilt_all = rebuild_serialized_file(parsed2, {1841: patched_i2})
            sc_rebuilt, sc_rep = SceneMonoFormat.patch_file_bytes(rebuilt_all, {"Smoke Button": "Botao Teste"})
            self.assertEqual(sc_rep["patched_strings"], 1)
            self.assertIn("Botao Teste", [sp.text for sp in scan_asset(parse_serialized_file(sc_rebuilt))])

            # Cross-verify synthetic v21 SerializedFile with UnityPy when available locally
            try:
                import UnityPy
            except ImportError:
                UnityPy = None
            if UnityPy is not None:
                env = UnityPy.load(sc_rebuilt)
                self.assertEqual(len(list(env.objects)), 3)

    def test_dll_format_patch_and_real_corpus_noop_roundtrip(self) -> None:
        smoke_dll = TW / "tests" / "fixtures" / "smoke-sample.dll"
        listed = DllFormat.list_strings(smoke_dll)
        self.assertIn("SmokeFixture.SampleDialog", listed)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            out_dll = tmp_dir / "patched.dll"
            DllFormat.patch(
                smoke_dll,
                out_dll,
                {"SmokeFixture.SampleDialog": {"Fixture Label": "Rotulo de Teste"}},
            )
            re_listed = DllFormat.list_strings(out_dll)
            self.assertIn("Rotulo de Teste", re_listed["SmokeFixture.SampleDialog"])
            self.assertNotIn("Fixture Label", re_listed["SmokeFixture.SampleDialog"])

        # Real EN/ smoke checks when game data is present
        en_root = ROOT / "EN"
        if (en_root / "The Iron Oath_Data" / "Managed" / "Assembly-CSharp.dll").is_file():
            native.verify_source_tree(en_root)
            sample_hjson = "The Iron Oath_Data/StreamingAssets/Data/Dialogue/DynamicDialogue/questDialogue.json"
            self.assertEqual(
                HjsonFormat.rebuild(en_root, sample_hjson, {}),
                (en_root / sample_hjson).read_bytes(),
            )
            sample_yarn = "The Iron Oath_Data/StreamingAssets/Data/QuestData/TioQuests.yarn.txt"
            self.assertEqual(
                YarnFormat.rebuild(en_root, sample_yarn, {}),
                (en_root / sample_yarn).read_bytes(),
            )
            self.assertEqual(
                CodexXmlFormat.rebuild(en_root, CodexXmlFormat.CODEX_PATH, {}),
                (en_root / CodexXmlFormat.CODEX_PATH).read_bytes(),
            )
            # Known logic check passes on empty/display-safe map
            usages = find_logic_usages(
                en_root / "The Iron Oath_Data" / "Managed" / "Assembly-CSharp.dll",
                {"DifficultyOptionsScreen": {"Starting Point": "Ponto de Partida"}},
            )
            self.assertEqual(usages, [])


if __name__ == "__main__":
    unittest.main()
