"""Protection and punctuation contract tests."""

import unittest

from localization.protection import (
    ProtectionError,
    protect,
    restore,
    validate_punctuation,
    validate_tokens,
)


class ProtectTests(unittest.TestCase):
    def test_reorders_independent_tokens(self):
        protected = protect("Take {0} damage to {1}!")
        assert protected.protected.count("__KEEP_") == 2
        validate_tokens(protected.tokens, "__KEEP_1__ dano em __KEEP_0__!")

    def test_missing_token_rejected(self):
        protected = protect("Take {0} damage!")
        with self.assertRaises(ProtectionError):
            validate_tokens(protected.tokens, "Tome dano!")

    def test_duplicated_token_rejected(self):
        protected = protect("Take {0} damage!")
        with self.assertRaises(ProtectionError):
            validate_tokens(protected.tokens, "__KEEP_0__ dano __KEEP_0__")

    def test_invented_token_rejected(self):
        protected = protect("Take damage!")
        with self.assertRaises(ProtectionError):
            validate_tokens(protected.tokens, "__KEEP_9__ dano")

    def test_number_protected(self):
        protected = protect("Deal 15 damage")
        assert protected.protected == "Deal __KEEP_0__ damage"

    def test_sprite_and_color_tags_protected(self):
        text = "<color=#4a9db5>Preservation:</color> <sprite name=\"levelUp\"> geração"
        protected = protect(text)
        assert "__KEEP_" in protected.protected
        assert len(protected.tokens) == 3
        tokens = [t.token for t in protected.tokens]
        # Sprite (independent) may move; colors (group) keep their order.
        validate_tokens(protected.tokens, f"geração {tokens[2]} {tokens[0]} {tokens[1]}")

    def test_unbalanced_paired_tag_rejected(self):
        protected = protect("<b>bold</b> text")
        with self.assertRaises(ProtectionError):
            validate_tokens(protected.tokens, "</b>negrito<b>")

    def test_group_order_fixed(self):
        text = "See https://example.com/a and https://example.com/b"
        protected = protect(text)
        tokens = [t.token for t in protected.tokens if t.category.value == "group"]
        assert len(tokens) == 2
        validate_tokens(protected.tokens, f"Veja {tokens[0]} e {tokens[1]}")
        with self.assertRaises(ProtectionError):
            validate_tokens(protected.tokens, f"Veja {tokens[1]} e {tokens[0]}")

    def test_restore_roundtrip(self):
        text = "Cost {0} gold, <b>now</b>."
        protected = protect(text)
        assert restore(protected.protected, protected.map_by_token) == text

    def test_restore_rejects_unknown(self):
        with self.assertRaises(ProtectionError):
            restore("__KEEP_7__ oops", {})


class PunctuationTests(unittest.TestCase):
    def test_diacritics_required(self):
        violations = validate_punctuation("Você não é o líder.")
        assert violations == []

    def test_asciified_portuguese_rejected(self):
        violations = validate_punctuation("Voce nao e o lider.")
        assert violations, "ASCII-fied Portuguese must be flagged"

    def test_curly_quotes_rejected(self):
        protected = protect("plain")
        with self.assertRaises(ProtectionError):
            validate_tokens(protected.tokens, "“Você não é o líder.”")

    def test_em_dash_rejected(self):
        protected = protect("plain")
        with self.assertRaises(ProtectionError):
            validate_tokens(protected.tokens, "Você — não é o líder.")

    def test_straight_quote_accepted(self):
        protected = protect("plain")
        validate_tokens(protected.tokens, '"Você não é o líder."')


if __name__ == "__main__":
    unittest.main()