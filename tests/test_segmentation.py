import unittest

from app.captions.segmenter import CaptionSegmenter
from app.core.models import TranscriptSegment, TranscriptionResult, Word


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
