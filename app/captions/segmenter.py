"""Segmentación de palabras en subtítulos legibles, limitada por tiempo y longitud."""

from app.core.models import Caption, SegmentationOptions, TranscriptionResult, Word

_SENTENCE_ENDINGS = (".", "!", "?", "…")
_CLAUSE_ENDINGS = (",", ";", ":")


class CaptionSegmenter:
    def segment(
        self,
        transcription: TranscriptionResult,
        options: SegmentationOptions = SegmentationOptions(),
    ) -> tuple[Caption, ...]:
        if options.max_words < 1 or options.max_characters < 1 or options.max_lines < 1:
            raise ValueError("Los límites de segmentación deben ser positivos.")
        words = [word for segment in transcription.segments for word in segment.words]
        captions: list[Caption] = []
        current: list[Word] = []
        for word in words:
            if current and self._should_break(current, word, options):
                captions.append(self._caption(current, options.min_duration))
                current = []
            current.append(word)
            if word.text.rstrip().endswith(_SENTENCE_ENDINGS):
                captions.append(self._caption(current, options.min_duration))
                current = []
        if current:
            captions.append(self._caption(current, options.min_duration))
        return tuple(captions)

    @staticmethod
    def _should_break(current: list[Word], next_word: Word, opts: SegmentationOptions) -> bool:
        text = " ".join(word.text for word in (*current, next_word))
        elapsed = next_word.end - current[0].start
        pause = max(0.0, next_word.start - current[-1].end)
        over_lines = _line_count(text, opts.max_characters) > opts.max_lines
        return (
            len(current) >= opts.max_words
            or len(text) > opts.max_characters and len(current) >= 2
            or elapsed > opts.max_duration
            or over_lines and len(current) >= 2
            or pause > opts.max_pause
            or current[-1].text.rstrip().endswith(_CLAUSE_ENDINGS)
            and not next_word.text.startswith(("¿", "¡"))
        )

    @staticmethod
    def _caption(words: list[Word], min_duration: float) -> Caption:
        start, end = words[0].start, words[-1].end
        if end - start < min_duration:
            end = start + min_duration
        return Caption(start=start, end=end, words=tuple(words))


def _line_count(text: str, max_characters: int) -> int:
    lines = 1
    length = 0
    for word in text.split():
        if length and length + 1 + len(word) > max_characters:
            lines += 1
            length = len(word)
        else:
            length += len(word) + (1 if length else 0)
    return lines
