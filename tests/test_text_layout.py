import os
import unittest
from math import isfinite

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QFont, QFontDatabase, QFontMetricsF
from PySide6.QtWidgets import QApplication

from app.captions.balance_text import balance_text, layout_captions
from app.core.exceptions import CaptionLayoutError
from app.core.models import Caption, CaptionStyleConfig, Word

def _font_family() -> str:
    families = QFontDatabase.families()
    for preferred in ("Arial", "Helvetica", "DejaVu Sans"):
        if preferred.casefold() in {family.casefold() for family in families}:
            return preferred
    return families[0]


def _layout(text: str, **overrides):
    options = {
        "font_family": _font_family(),
        "font_weight": 700,
        "base_font_size": 110,
        "min_font_size": 56,
        "max_width": 920,
        "max_height": 1200,
        "max_lines": 3,
        "line_height": 132,
        "stroke_width": 4,
        "letter_spacing": 0,
    }
    options.update(overrides)
    return balance_text(text, **options)


class BalanceTextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_day_95_balances_whole_words_and_avoids_function_word_endings(self) -> None:
        layout = _layout("DÍA 95 DEL RETO INMOBILIARIO")

        self.assertFalse(layout.overflow)
        self.assertEqual(len(layout.lines), 3)
        self.assertEqual(" ".join(layout.lines), "DÍA 95 DEL RETO INMOBILIARIO")
        self.assertTrue(
            all(
                line.split()[-1].casefold() not in {"de", "del", "en", "la", "y"}
                for line in layout.lines[:-1]
            )
        )
        self.assertLessEqual(layout.estimated_width, 920)
        self.assertLessEqual(layout.estimated_height, 1200)
        self.assertTrue(
            all(
                isfinite(value)
                for value in (
                    layout.font_size,
                    layout.line_height,
                    layout.estimated_width,
                    layout.estimated_height,
                )
            )
        )

    def test_day_100_does_not_depend_on_fixed_character_count(self) -> None:
        layout = _layout("DÍA 100 DEL RETO INMOBILIARIO")

        self.assertFalse(layout.overflow)
        self.assertEqual(" ".join(layout.lines), "DÍA 100 DEL RETO INMOBILIARIO")

    def test_short_word_stays_on_one_line(self) -> None:
        layout = _layout("RETO")

        self.assertEqual(layout.lines, ("RETO",))

    def test_wide_single_word_shrinks_without_splitting(self) -> None:
        layout = _layout(
            "INMOBILIARIO",
            base_font_size=220,
            min_font_size=56,
            max_width=420,
        )

        self.assertFalse(layout.overflow)
        self.assertEqual(layout.lines, ("INMOBILIARIO",))
        self.assertLess(layout.font_size, 220)
        self.assertLessEqual(layout.estimated_width, 420)

    def test_wide_and_narrow_glyphs_use_real_font_metrics(self) -> None:
        narrow = _layout("IIIIIIIIII", max_lines=1, max_width=800)
        wide = _layout("WWWWWWWWWW", max_lines=1, max_width=800)

        self.assertFalse(narrow.overflow)
        self.assertFalse(wide.overflow)
        self.assertLess(wide.font_size, narrow.font_size)
        font = QFont(_font_family())
        font.setPixelSize(narrow.font_size)
        font.setWeight(QFont.Weight(700))
        expected = QFontMetricsF(font).horizontalAdvance("IIIIIIIIII") + 8
        self.assertAlmostEqual(narrow.estimated_width, expected, delta=2)

    def test_stroke_is_included_in_width_and_height(self) -> None:
        thin = _layout("RETO", stroke_width=0)
        thick = _layout("RETO", stroke_width=30)

        self.assertFalse(thick.overflow)
        self.assertLessEqual(thick.font_size, thin.font_size)
        self.assertGreater(thick.estimated_width, 60)
        self.assertGreater(thick.estimated_height, thin.estimated_height)

    def test_missing_font_returns_identifiable_layout_failure(self) -> None:
        layout = _layout("RETO", font_family="Definitely Missing Font")

        self.assertTrue(layout.overflow)
        self.assertIn("no está instalada", layout.failure_reason)

    def test_generated_caption_raises_a_presentable_error_if_font_is_missing(self) -> None:
        caption = Caption(0, 1, (Word("RETO", 0, 0.8),))

        with self.assertRaisesRegex(CaptionLayoutError, "no está instalada"):
            layout_captions(
                (caption,),
                CaptionStyleConfig(font_family="Definitely Missing Font"),
                1080,
                1920,
            )

    def test_generated_captions_fit_multiple_video_resolutions(self) -> None:
        caption = Caption(
            0,
            1,
            tuple(
                Word(word, index * 0.1, index * 0.1 + 0.08)
                for index, word in enumerate("DÍA 100 DEL RETO INMOBILIARIO".split())
            ),
        )

        for width, height in ((1080, 1920), (720, 1280)):
            with self.subTest(resolution=(width, height)):
                captions, font_sizes = layout_captions(
                    (caption,), CaptionStyleConfig(font_size=110), width, height
                )
                self.assertEqual(len(font_sizes), 1)
                self.assertEqual(
                    " ".join(captions[0].text.split()),
                    "DÍA 100 DEL RETO INMOBILIARIO",
                )
                lines = captions[0].text.splitlines()
                self.assertEqual(len(lines), 2)
                self.assertNotIn(lines[0].split()[-1].casefold(), {"de", "del", "en", "la", "y"})

    def test_reported_caption_fits_clean_and_corrupted_in_landscape_and_portrait(self) -> None:
        for text in (
            "la mañana para terminar todos los pendientes",
            "la mañana para terminar todos los pendientesạẠồị",
            "RETO",
        ):
            words = tuple(
                Word(word, index * 0.1, index * 0.1 + 0.08)
                for index, word in enumerate(text.split())
            )
            caption = Caption(0, len(words) * 0.1, words)
            for width, height in ((1920, 1080), (1080, 1920)):
                with self.subTest(text=text, resolution=(width, height)):
                    captions, font_sizes = layout_captions(
                        (caption,), CaptionStyleConfig(font_size=110), width, height
                    )
                    self.assertEqual(
                        " ".join(captions[0].text.split()), text
                    )
                    self.assertGreaterEqual(font_sizes[0], 56)

    def test_long_word_uses_hyphenated_fallback_in_narrow_vertical_video(self) -> None:
        caption = Caption(
            0,
            1,
            (Word("anticonstitucionalmente", 0, 0.8),),
        )

        captions, font_sizes = layout_captions(
            (caption,), CaptionStyleConfig(font_size=110), 360, 640
        )

        self.assertEqual(font_sizes, (56,))
        self.assertGreater(len(captions[0].words), 1)
        self.assertTrue(all(word.text.endswith("-") for word in captions[0].words[:-1]))
        self.assertTrue(captions[0].line_breaks)

    def test_reported_caption_uses_fallback_in_low_resolution_vertical_video(self) -> None:
        text = "la mañana para terminar todos los pendientesạẠồị"
        words = tuple(
            Word(word, index * 0.1, index * 0.1 + 0.08)
            for index, word in enumerate(text.split())
        )
        caption = Caption(0, 0.8, words)

        with self.assertLogs("app.captions.balance_text", level="WARNING") as logs:
            captions, font_sizes = layout_captions(
                (caption,), CaptionStyleConfig(font_size=110), 360, 640
            )

        self.assertEqual(font_sizes, (56,))
        self.assertTrue(captions[0].line_breaks)
        self.assertIn("ajuste de emergencia", logs.output[0])


if __name__ == "__main__":
    unittest.main()
