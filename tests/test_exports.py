import unittest

from app.ass.generator import ASSGenerator
from app.core.models import Caption, CaptionStyleConfig, Word
from app.srt.generator import SRTGenerator


def _captions() -> tuple[Caption, ...]:
    return (
        Caption(0, 1, (Word("Hola", 0, 0.4), Word("mundo.", 0.4, 0.8))),
    )


class ExportTests(unittest.TestCase):
    def test_srt_has_standard_timestamps_and_plain_text(self) -> None:
        output = SRTGenerator().generate(_captions())
        self.assertIn("00:00:00,000 --> 00:00:01,000", output)
        self.assertIn("Hola mundo.", output)

    def test_ass_generates_highlighted_word_events(self) -> None:
        output = ASSGenerator().generate(_captions(), "tiktok_pop", CaptionStyleConfig())
        self.assertIn("[Events]", output)
        self.assertIn(r"\1c&H0000E6FF", output)
        self.assertIn("Dialogue:", output)

    def test_one_word_style_emits_one_event_per_word(self) -> None:
        output = ASSGenerator().generate(_captions(), "one_word")
        self.assertEqual(output.count("Dialogue:"), 2)

    def test_ass_applies_normalized_caption_layout(self) -> None:
        output = ASSGenerator().generate(
            _captions(),
            "tiktok_pop",
            CaptionStyleConfig(),
            1920,
            1080,
            caption_layouts=((0.25, 0.75, 1.5, 4.0, 0.0),),
        )
        self.assertIn(r"{\an5\pos(480,810)\fscx150\fscy150\fsp4}", output)

    def test_ass_applies_normalized_rotation(self) -> None:
        output = ASSGenerator().generate(
            _captions(),
            "tiktok_pop",
            CaptionStyleConfig(),
            caption_layouts=((0.5, 0.5, 1, 0, -15),),
        )
        self.assertIn(r"\frz-15", output)

    def test_ass_maps_typography_bubble_case_and_effects(self) -> None:
        config = CaptionStyleConfig(
            bold=True,
            italic=True,
            underline=True,
            case_mode="upper",
            bubble_enabled=True,
            bubble_opacity=50,
            bubble_padding=12,
            background_color="#123456",
            blur=2,
        )
        output = ASSGenerator().generate(_captions(), "minimal", config)
        self.assertIn("&H80563412", output)
        self.assertIn(",-1,-1,-1,0,100,100,0,0,3,12,", output)
        self.assertIn(r"{\blur2}HOLA", output)

    def test_multiline_caption_exports_line_breaks_to_ass_and_srt(self) -> None:
        caption = Caption(
            0,
            1,
            (Word("Hola", 0, 0.4), Word("mundo", 0.4, 0.8)),
            line_breaks=(0,),
        )
        ass = ASSGenerator().generate(
            (caption,), "minimal", CaptionStyleConfig(highlight_enabled=False)
        )
        srt = SRTGenerator().generate((caption,))
        self.assertIn(r"Hola\Nmundo", ass)
        self.assertIn("Hola\nmundo", srt)

    def test_line_spacing_positions_lines_in_ass_play_resolution(self) -> None:
        caption = Caption(
            0,
            1,
            (Word("Hola", 0, 0.4), Word("mundo", 0.4, 0.8)),
            line_breaks=(0,),
        )
        output = ASSGenerator().generate(
            (caption,),
            "minimal",
            CaptionStyleConfig(highlight_enabled=False, line_spacing=20),
            1920,
            1080,
            caption_layouts=((0.5, 0.5, 1, 0),),
        )
        self.assertIn(r"\pos(960,498)", output)
        self.assertIn(r"\pos(960,582)", output)
