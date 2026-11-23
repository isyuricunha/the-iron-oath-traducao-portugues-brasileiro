from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

TW = Path(__file__).resolve().parent.parent
if str(TW) not in sys.path:
    sys.path.insert(0, str(TW))

from formats.scene_mono_format import TMP_TEXT_SCRIPT
from formats.unity_assets import (
    CLASS_ID_MONOBEHAVIOUR,
    CLASS_ID_TEXT_ASSET,
    build_minimal_assets,
    encode_text_asset,
)
from native import Invalid
from storage import json_bytes
from toolkit import Project


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


def _populate_game_tree(target_root: Path, *, yarn_line2: str = "Guard the gate.", extra_hjson: bool = False) -> None:
    data = target_root / "The Iron Oath_Data"
    (data / "Managed").mkdir(parents=True, exist_ok=True)
    shutil.copy2(TW / "tests" / "fixtures" / "smoke-sample.dll", data / "Managed" / "Assembly-CSharp.dll")

    xml_body = '<?xml version="1.0" encoding="utf-8"?><Localization><TextKey name="k"><EN>Hello</EN></TextKey></Localization>'
    assets = build_minimal_assets(
        [
            (96, CLASS_ID_TEXT_ASSET, encode_text_asset("LocalizationText", xml_body)),
            (1841, CLASS_ID_MONOBEHAVIOUR, _synth_i2_raw()),
            (2000, CLASS_ID_MONOBEHAVIOUR, _synth_scene_raw("Play Game")),
        ]
    )
    (data / "resources.assets").write_bytes(assets)

    sa = data / "StreamingAssets" / "Data"
    (sa / "QuestData").mkdir(parents=True, exist_ok=True)
    (sa / "Codex").mkdir(parents=True, exist_ok=True)
    (sa / "WorldSim").mkdir(parents=True, exist_ok=True)

    (sa / "QuestData" / "TioQuests.yarn.txt").write_bytes(
        (
            "title: SE.Intro\r\n"
            "---\r\n"
            "{mainChar0}Hold the line!\r\n"
            f"{yarn_line2}\r\n"
            "->[Action:Continue]\r\n"
            "===\r\n"
        ).encode("utf-8")
    )
    (sa / "Codex" / "codex.xml").write_bytes(
        b'<CodexDoc><codexCategory name="Annals" id="annals"><description>Ancient history.</description></codexCategory></CodexDoc>'
    )
    (sa / "WorldSim" / "codex.json").write_bytes(
        b'{\r\n\tTitle: "World Lore",\r\n\tText: "Caelum endures.",\r\n}\r\n'
    )
    if extra_hjson:
        (sa / "WorldSim" / "extra.json").write_bytes(
            b'{\r\n\tName: "New Crest",\r\n}\r\n'
        )


def _init_sandbox_project(root: Path) -> Project:
    tw = root / "translation-work"
    tw.mkdir(parents=True, exist_ok=True)
    shapes = {
        "schema": 1,
        "game": "The Iron Oath",
        "native_version": "1.0.021",
        "strip_diacritics_on_build": True,
    }
    policy = {
        "schema": 1,
        "dll_scope": {"SmokeFixture.SampleDialog": ["Fixture Label"]},
        "scene_ignore": [],
        "i2_skip_terms": [],
    }
    shapes_b = (json.dumps(shapes, indent=2) + "\n").encode("utf-8")
    policy_b = (json.dumps(policy, indent=2) + "\n").encode("utf-8")
    (tw / "engine-shapes.json").write_bytes(shapes_b)
    (tw / "source-policy.json").write_bytes(policy_b)

    tc = {
        "schema": 1,
        "game": "The Iron Oath",
        "files": {
            "translation-work/engine-shapes.json": {"mode": "git-lf", "sha256": hashlib.sha256(shapes_b).hexdigest()},
            "translation-work/source-policy.json": {"mode": "git-lf", "sha256": hashlib.sha256(policy_b).hexdigest()},
        },
    }
    tc_b = (json.dumps(tc, indent=2) + "\n").encode("utf-8")
    (tw / "toolchain.json").write_bytes(tc_b)
    rt = {"schema": 1, "toolchain_sha256": hashlib.sha256(tc_b).hexdigest()}
    (tw / "runtime-support.json").write_bytes((json.dumps(rt, indent=2) + "\n").encode("utf-8"))

    _populate_game_tree(root / "EN")
    return Project(root)


class ToolkitSyncAndBuildTests(unittest.TestCase):
    def test_extract_apply_status_sync_build_verify_and_package_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proj = _init_sandbox_project(root)

            ext = proj.extract(batch_size=3)
            self.assertEqual(ext["status"], "extracted")
            self.assertEqual(ext["entries"], 10)
            self.assertEqual(ext["batches"], 4)

            # status & resume must NOT invoke dotnet subprocess
            with mock.patch("subprocess.run", side_effect=AssertionError("dotnet must not run in status")):
                st = proj.status()
            self.assertEqual(st["completed"], 0)
            self.assertEqual(st["pending"], 10)
            self.assertEqual(st["PTBR"], "absent")

            manifest, rows, _mem, _exp = proj.load()
            gen = manifest["generation"]

            # Reject wrong generation, unknown ID, duplicate ID
            bad_gen = root / "bad_gen.json"
            bad_gen.write_bytes(json_bytes({"generation": "wrong", "entries": []}))
            with self.assertRaises(Invalid) as e1:
                proj.apply(bad_gen)
            self.assertIn("WRONG_GENERATION", str(e1.exception))

            bad_id = root / "bad_id.json"
            bad_id.write_bytes(json_bytes({"generation": gen, "entries": [{"id": "missing:1", "PTBR": "x"}]}))
            with self.assertRaises(Invalid) as e2:
                proj.apply(bad_id)
            self.assertIn("UNKNOWN_ID", str(e2.exception))

            dup_id = root / "dup_id.json"
            dup_id.write_bytes(
                json_bytes(
                    {
                        "generation": gen,
                        "entries": [{"id": rows[0]["id"], "PTBR": "x"}, {"id": rows[0]["id"], "PTBR": "y"}],
                    }
                )
            )
            with self.assertRaises(Invalid) as e3:
                proj.apply(dup_id)
            self.assertIn("DUPLICATE_ID", str(e3.exception))

            # Translate 9 entries cleanly, leave 1 pending, test overwrite refusal without --replace
            payload = root / "apply.json"
            payload.write_bytes(
                json_bytes(
                    {
                        "generation": gen,
                        "entries": [
                            {"id": r["id"], "source_fingerprint": r["source_fingerprint"], "PTBR": f"T: {r['EN']} ação"}
                            for r in rows[:-1]
                        ],
                    }
                )
            )
            res = proj.apply(payload)
            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["applied"], 9)

            # Overwrite denied without --replace
            ow = root / "overwrite.json"
            ow.write_bytes(json_bytes({"generation": gen, "entries": [{"id": rows[0]["id"], "PTBR": "Outro"}]}))
            ow_res = proj.apply(ow, replace=False)
            self.assertEqual(ow_res["rejected"], 1)
            self.assertIn("OVERWRITE_DENIED", ow_res["rejected_details"][0]["reason"])
            self.assertEqual(proj.apply(ow, replace=True)["applied"], 1)

            # Refuses build when 1 entry is still pending
            with self.assertRaises(Invalid) as inc_err:
                proj.build()
            self.assertIn("INCOMPLETE_TRANSLATION", str(inc_err.exception))

            # Complete final entry
            last = rows[-1]
            payload_last = root / "apply_last.json"
            payload_last.write_bytes(
                json_bytes({"generation": gen, "entries": [{"id": last["id"], "PTBR": f"T: {last['EN']}"}]})
            )
            proj.apply(payload_last)

            # Check passes; editing EN directly makes check fail until sync
            self.assertEqual(proj.check()["status"], "ok")
            yarn_en = root / "EN" / "The Iron Oath_Data" / "StreamingAssets" / "Data" / "QuestData" / "TioQuests.yarn.txt"
            saved_yarn = yarn_en.read_bytes()
            yarn_en.write_bytes(saved_yarn.replace(b"Guard the gate.", b"Watch the gate."))
            with self.assertRaises(Invalid) as ch_err:
                proj.check()
            self.assertIn("SOURCE_CHANGED", str(ch_err.exception))

            # diff reads changes without mutating batches
            drep = proj.diff()
            self.assertEqual(drep["counts"]["modified"], 1)
            self.assertEqual(drep["counts"]["preserved_translations"], 9)

            # sync reconciles EN edit: modified line goes pending with previous_EN/previous_PTBR; other 9 stay translated
            srep = proj.sync()
            self.assertEqual(srep["counts"]["modified"], 1)
            self.assertEqual(srep["counts"]["preserved_translations"], 9)

            manifest2, rows2, _, _ = proj.load()
            mod_row = next(r for r in rows2 if r["EN"] == "Watch the gate.")
            self.assertEqual(mod_row["PTBR"], "")
            self.assertEqual(mod_row["context"]["previous_EN"], "Guard the gate.")

            # Re-translate modified line & verify complete build -> verify-output -> package
            p_mod = root / "apply_mod.json"
            p_mod.write_bytes(
                json_bytes({"generation": manifest2["generation"], "entries": [{"id": mod_row["id"], "PTBR": "Vigie o portão."}]})
            )
            proj.apply(p_mod)
            built = proj.build()
            self.assertEqual(built["status"], "built")
            self.assertTrue(built["strip_diacritics"])

            # Verify output with no dotnet calls
            with mock.patch("subprocess.run", side_effect=AssertionError("dotnet must not run in verify-output/package")):
                ver = proj.verify_output()
                self.assertEqual(ver["status"], "verified")
                pkg = proj.package()
                self.assertEqual(pkg["status"], "packaged")

            with zipfile.ZipFile(root / "PTBR.zip") as zf:
                names = zf.namelist()
                self.assertTrue(all(n.startswith("The Iron Oath_Data/") for n in names))
                # Confirm diacritics were stripped in PTBR output
                codex_out = zf.read("The Iron Oath_Data/StreamingAssets/Data/Codex/codex.xml").decode("utf-8")
                self.assertIn("acao", codex_out)
                self.assertNotIn("ação", codex_out)

            # Tampering PTBR file must fail verify_output
            ptbr_codex = root / "PTBR" / "The Iron Oath_Data" / "StreamingAssets" / "Data" / "Codex" / "codex.xml"
            saved_codex = ptbr_codex.read_bytes()
            ptbr_codex.write_bytes(saved_codex + b"\n")
            with self.assertRaises(Invalid) as tmp_err:
                proj.verify_output()
            self.assertIn("PTBR_HASH_MISMATCH", str(tmp_err.exception))
            ptbr_codex.write_bytes(saved_codex)

            # Adding unexpected file in PTBR/ must fail verify_output
            extra_f = root / "PTBR" / "The Iron Oath_Data" / "rogue.txt"
            extra_f.write_bytes(b"rogue")
            with self.assertRaises(Invalid) as inv_err:
                proj.verify_output()
            self.assertIn("PTBR_INVENTORY_MISMATCH", str(inv_err.exception))

    def test_update_sources_with_spaces_autodetect_and_safety_guards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proj = _init_sandbox_project(root)
            proj.extract()

            # Build complete PTBR to also test TRANSLATED_SOURCE guard
            _, rows, _, _ = proj.load()
            all_p = root / "all.json"
            all_p.write_bytes(
                json_bytes(
                    {
                        "generation": proj.load()[0]["generation"],
                        "entries": [{"id": r["id"], "PTBR": f"PT {r['EN']}"} for r in rows],
                    }
                )
            )
            proj.apply(all_p)
            proj.build()

            # Reject PTBR directly AND PTBR copied outside pretending to be an EN game install
            with self.assertRaises(Invalid) as pt_err:
                proj.update_sources(game_root=root / "PTBR")
            self.assertIn("INVALID_GAME_ROOT", str(pt_err.exception))

            disguised = root / "DisguisedInstall"
            shutil.copytree(root / "PTBR", disguised)
            with self.assertRaises(Invalid) as tr_err:
                proj.update_sources(game_root=disguised)
            self.assertIn("matches translated PTBR build output", str(tr_err.exception))
            with self.assertRaises(Invalid) as sc_err:
                proj.sync_candidate(disguised)
            self.assertIn("TRANSLATED_SOURCE", str(sc_err.exception))

            # Explicit --game-root with spaces & new file in candidate install
            spaced = root / "Program Files (x86)" / "GOG Games" / "The Iron Oath"
            _populate_game_tree(spaced, yarn_line2="Defend the gate.", extra_hjson=True)
            dry_res = proj.update_sources(game_root=spaced, dry_run=True)
            self.assertEqual(dry_res["counts"]["added"], 1)
            self.assertEqual(dry_res["counts"]["modified"], 1)
            # dry-run did not update generation
            self.assertFalse((root / "EN" / "The Iron Oath_Data" / "StreamingAssets" / "Data" / "WorldSim" / "extra.json").exists())

            live_res = proj.update_sources(game_root=spaced, dry_run=False)
            self.assertEqual(live_res["counts"]["added"], 1)
            self.assertTrue((root / "EN" / "The Iron Oath_Data" / "StreamingAssets" / "Data" / "WorldSim" / "extra.json").exists())

            # Autodetect failure error includes explicit --game-root hint
            with mock.patch.object(proj, "_discover_installed_game_roots", return_value=[]):
                with self.assertRaises(Invalid) as miss_err:
                    proj.update_sources()
                self.assertIn("update-sources --game-root", str(miss_err.exception))

            # Incomplete source root refuses sync
            broken_src = root / "incomplete_src"
            (broken_src / "The Iron Oath_Data").mkdir(parents=True)
            with self.assertRaises(Invalid) as inc_src:
                proj.sync(source_root=broken_src)
            self.assertIn("INCOMPLETE_SOURCE", str(inc_src.exception))


if __name__ == "__main__":
    unittest.main()
