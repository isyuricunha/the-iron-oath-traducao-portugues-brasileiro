"""Unit tests for the manual-target token contract."""

import unittest

from localization.protection import ProtectionError, validate_manual_target


class ValidateManualTargetTest(unittest.TestCase):
    def test_returns_target_unchanged_when_clean(self):
        source = 'Cost: {0}<sprite name="coin">'
        target = 'Custo: {0}<sprite name="coin">'
        self.assertEqual(validate_manual_target(source, target), target)

    def test_placeholder_must_survive(self):
        with self.assertRaises(ProtectionError):
            validate_manual_target("{0} gold", "ouro")

    def test_numbers_must_not_change(self):
        with self.assertRaises(ProtectionError):
            validate_manual_target("3 days", "3 dias (3)")

    def test_ascii_fied_word_rejected(self):
        with self.assertRaises(ProtectionError):
            validate_manual_target("x", "nao foi possivel")

    def test_unbalanced_color_rejected(self):
        with self.assertRaises(ProtectionError):
            validate_manual_target("a", "<color=#fff>b</i>")

    def test_escape_runs_are_aligned_to_source(self):
        source = "First line\\nSecond line"
        target = "Primeira linha\\\\nSegunda linha"  # double backslash
        aligned = validate_manual_target(source, target)
        self.assertEqual(aligned, "Primeira linha\\nSegunda linha")

    def test_repositioned_tokens_are_allowed(self):
        source = "{0} of {1}"
        target = "{1} de {0}"
        self.assertEqual(validate_manual_target(source, target), target)


if __name__ == "__main__":
    unittest.main()
