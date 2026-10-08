import unittest

from app.core.models import (
    CaptionStyleConfig,
    SegmentationOptions,
    TranscriptSegment,
    TranscriptionResult,
    Word,
)
from app.styles.registry import StyleRegistry


class DomainTests(unittest.TestCase):
    def test_invalid_word_timestamps_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Word("error", 2, 1)

    def test_invalid_style_config_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            CaptionStyleConfig(font_size=500)

    def test_style_registry_contains_five_presets(self) -> None:
        registry = StyleRegistry()
        self.assertEqual(len(registry.all()), 5)
        self.assertEqual(registry.get("karaoke").behavior, "karaoke")

    def test_transcript_exposes_flat_words_duration_and_segmentation_defaults(self) -> None:
        words = (Word("Hola", 0.1, 0.5), Word("mundo", 0.7, 1.1))
        result = TranscriptionResult("es", (TranscriptSegment(0.1, 1.1, words),))
        self.assertEqual(result.words, words)
        self.assertEqual(result.duration, 1.1)
        self.assertEqual(SegmentationOptions().max_words, 7)
        self.assertEqual(SegmentationOptions().max_duration, 3.5)
        from app.core.models import TranscriptionOptions

        self.assertEqual(TranscriptionOptions().compute_type, "int8")
