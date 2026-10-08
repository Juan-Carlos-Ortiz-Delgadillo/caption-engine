import unittest

from app.captions.segmenter import CaptionSegmenter
from app.core.models import (
    SegmentationOptions,
    TranscriptSegment,
    TranscriptionResult,
    Word,
)


def _result(texts: list[str], step: float = 0.3) -> TranscriptionResult:
    words = tuple(
        Word(text, index * step, index * step + 0.2)
        for index, text in enumerate(texts)
    )
    return TranscriptionResult("es", (TranscriptSegment(0, words[-1].end, words),))


class SegmentationTests(unittest.TestCase):
    def test_splits_at_sentence_punctuation_and_keeps_word_times(self) -> None:
        result = _result(["Hola,", "¿cómo", "estás?", "Muy", "bien."])
        captions = CaptionSegmenter().segment(result)
        self.assertEqual(
            [caption.text for caption in captions], ["Hola, ¿cómo estás?", "Muy bien."]
        )
        self.assertEqual(captions[0].words[0].start, 0)

    def test_splits_long_text_without_losing_words(self) -> None:
        words = ["una", "frase", "bastante", "larga", "para", "dividir"]
        captions = CaptionSegmenter().segment(_result(words))
        self.assertEqual([word.text for caption in captions for word in caption.words], words)
        self.assertTrue(all(len(caption.words) <= 8 for caption in captions))

    def test_balances_lines_and_avoids_ending_a_line_with_a_function_word(self) -> None:
        words = ["ESTO", "ES", "DE", "TRABAJAR", "EN", "LA", "COMPUTADORA"]
        options = SegmentationOptions(
            max_words=10,
            max_characters=20,
            max_duration=10,
            max_pause=10,
        )

        captions = CaptionSegmenter().segment(_result(words), options)

        self.assertEqual(len(captions), 1)
        self.assertEqual(captions[0].line_breaks, (3,))
        self.assertEqual(captions[0].text, "ESTO ES DE TRABAJAR\nEN LA COMPUTADORA")
