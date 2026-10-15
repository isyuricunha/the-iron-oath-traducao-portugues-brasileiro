"""Integration test for the DLL string patcher (requires dotnet + the game DLL)."""

import tempfile
import unittest
from pathlib import Path

from localization.formats.dll_format import DllFormat

REPO = Path(__file__).resolve().parents[1]
DLL = REPO / "The Iron Oath_Data" / "Managed" / "Assembly-CSharp.dll"


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


if __name__ == "__main__":
    unittest.main()
