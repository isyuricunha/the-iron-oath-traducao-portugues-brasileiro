"""Integration test for the DLL string patcher (requires dotnet + the game DLL)."""

import tempfile
import unittest
from pathlib import Path

from localization.formats.dll_format import DllFormat, find_logic_usages

REPO = Path(__file__).resolve().parents[1]
DLL = REPO / "The Iron Oath_Data" / "Managed" / "Assembly-CSharp.dll"
MAP = REPO / "interface-translations.json"


@unittest.skipUnless(DLL.exists(), "game assembly not available")
class DllFormatTest(unittest.TestCase):
    def test_patch_and_verify_two_strings(self):
        scoped = {
            "DifficultyOptionsScreen": {
                "Starting Point": "Ponto de Partida",
                "After Prologue": "Depois do Prólogo",
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "patched.dll"
            report = DllFormat.patch(REPO, out, scoped)
            self.assertGreaterEqual(report["total"], 2)
            problems = DllFormat.validate_patched(out, scoped)
            self.assertEqual(problems, [])

    def test_committed_map_has_no_logic_usages(self):
        """The published map must never contain a string the game compares."""
        self.assertTrue(MAP.exists())
        usages = find_logic_usages(DLL, MAP)
        self.assertEqual(usages, [])


if __name__ == "__main__":
    unittest.main()
