"""Markdown image reference handling: rewriting and usage ordering."""
from __future__ import annotations

import unittest

import support  # noqa: F401 - sets sys.path
import mdlinks


class LinkScanTests(unittest.TestCase):
    def test_inline_destination_is_rewritten_and_caption_kept(self):
        text = "# Paper\n\n![Figure 1](assets/f1.jpg \"A caption\")\n"
        result = mdlinks.rewrite(text, {"assets/f1.jpg": "images/f1.png"})
        self.assertIn('![Figure 1](images/f1.png "A caption")', result)
        self.assertIn("# Paper", result)

    def test_code_examples_are_not_rewritten(self):
        text = (
            "Real: ![x](assets/a.png)\n\n"
            "```markdown\n![x](assets/a.png)\n```\n\n"
            "Inline: `![x](assets/a.png)`\n\n"
            "<!-- ![x](assets/a.png) -->\n"
        )
        result = mdlinks.rewrite(text, {"assets/a.png": "images/a.png"})
        self.assertEqual(result.count("images/a.png"), 1)
        self.assertEqual(result.count("assets/a.png"), 3)

    def test_html_img_src_is_rewritten(self):
        text = '<img src="assets/d.svg" alt="d">\n'
        result = mdlinks.rewrite(text, {"assets/d.svg": "images/d.svg"})
        self.assertIn('src="images/d.svg"', result)

    def test_reference_style_definition_is_rewritten(self):
        text = "![one][fig1]\n\n![two][fig2]\n\n[fig1]: assets/f1.png\n[fig2]: assets/f2.png\n"
        result = mdlinks.rewrite(text, {"assets/f1.png": "images/f1.png",
                                        "assets/f2.png": "images/f2.png"})
        self.assertNotIn("assets/", result)
        self.assertIn("[fig1]: images/f1.png", result)
        self.assertIn("[fig2]: images/f2.png", result)

    def test_usage_order_follows_document_not_definition_position(self):
        text = (
            "![a][two]\n\n"
            "![b](images/b.png)\n\n"
            "![c][one]\n\n"
            "[one]: images/one.png\n"
            "[two]: images/two.png\n"
        )
        self.assertEqual(mdlinks.image_refs(text),
                         ("images/two.png", "images/b.png", "images/one.png"))

    def test_ambiguous_basename_is_never_guessed(self):
        text = "![x](other/figure.png)\n"
        self.assertEqual(mdlinks.rewrite(text, {"figure.png": "images/figure.png"}), text)

    def test_canonical_ref_normalizes_separators_and_escapes(self):
        self.assertEqual(mdlinks.canonical_ref(".\\images\\a%20b.png"), "images/a b.png")


if __name__ == "__main__":
    unittest.main()
