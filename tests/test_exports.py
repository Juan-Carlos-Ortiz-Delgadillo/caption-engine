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
