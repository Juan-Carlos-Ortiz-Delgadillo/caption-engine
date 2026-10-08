"""Segmentación de palabras en subtítulos legibles, limitada por tiempo y longitud."""

from functools import lru_cache

from app.core.models import Caption, SegmentationOptions, TranscriptionResult, Word

_SENTENCE_ENDINGS = (".", "!", "?", "…")
_CLAUSE_ENDINGS = (",", ";", ":")
_FUNCTION_WORDS = {
    "a",
    "al",
    "con",
    "como",
    "de",
    "del",
    "e",
    "el",
    "en",
    "entre",
    "hacia",
    "hasta",
    "la",
    "las",
    "le",
    "les",
    "lo",
    "los",
    "me",
    "mi",
    "ni",
    "o",
    "para",
    "por",
    "que",
    "se",
    "sin",
    "su",
    "te",
    "tu",
    "un",
    "una",
    "unas",
    "unos",
    "y",
}


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
                captions.append(
                    self._caption(current, options.min_duration, options.max_characters)
                )
                current = []
            current.append(word)
            if word.text.rstrip().endswith(_SENTENCE_ENDINGS):
                captions.append(
                    self._caption(current, options.min_duration, options.max_characters)
                )
                current = []
        if current:
            captions.append(
                self._caption(current, options.min_duration, options.max_characters)
            )
        return tuple(captions)

    @staticmethod
    def _should_break(current: list[Word], next_word: Word, opts: SegmentationOptions) -> bool:
        elapsed = next_word.end - current[0].start
        pause = max(0.0, next_word.start - current[-1].end)
        over_lines = (
            _minimum_line_count((*current, next_word), opts.max_characters) > opts.max_lines
        )
        return (
            len(current) >= opts.max_words
            or elapsed > opts.max_duration
            or over_lines
            or pause > opts.max_pause
            or current[-1].text.rstrip().endswith(_CLAUSE_ENDINGS)
            and not next_word.text.startswith(("¿", "¡"))
        )

    @staticmethod
    def _caption(words: list[Word], min_duration: float, max_characters: int) -> Caption:
        start, end = words[0].start, words[-1].end
        if end - start < min_duration:
            end = start + min_duration
        line_breaks = _balanced_line_breaks(words, max_characters)
        return Caption(start=start, end=end, words=tuple(words), line_breaks=line_breaks)


def _minimum_line_count(words: tuple[Word, ...], max_characters: int) -> int:
    line_count = 0
    line_width = 0
    line_has_words = False
    for word in words:
        word_width = len(word.text)
        if word_width > max_characters:
            line_count += 1
            line_width = 0
            line_has_words = False
        elif line_has_words and line_width + 1 + word_width <= max_characters:
            line_width += 1 + word_width
        else:
            line_count += 1
            line_width = word_width
            line_has_words = True
    return line_count


def _balanced_line_breaks(words: list[Word], max_characters: int) -> tuple[int, ...]:
    line_count = _minimum_line_count(tuple(words), max_characters)
    if line_count == 1:
        return ()

    total_width = sum(len(word.text) for word in words) + len(words) - line_count

    def find_layout(avoid_function_word_endings: bool) -> tuple[int, ...] | None:
        @lru_cache(maxsize=None)
        def best(
            start: int, lines_remaining: int
        ) -> tuple[int, int, tuple[int, ...]] | None:
            words_remaining = len(words) - start
            if lines_remaining == 0:
                return (0, 0, ()) if start == len(words) else None
            if words_remaining < lines_remaining:
                return None

            line_width = 0
            best_result: tuple[int, int, tuple[int, ...]] | None = None
            for end in range(start + 1, len(words) + 1):
                word_width = len(words[end - 1].text)
                if end == start + 1:
                    line_width = word_width
                else:
                    line_width += 1 + word_width
                oversized_single_word = end == start + 1 and word_width > max_characters
                if line_width > max_characters and not oversized_single_word:
                    break
                if (
                    avoid_function_word_endings
                    and end < len(words)
                    and _is_function_word(words[end - 1].text)
                ):
                    continue

                suffix = best(end, lines_remaining - 1)
                if suffix is None:
                    continue
                variance = (line_width * line_count - total_width) ** 2 + suffix[0]
                semantic_penalty = (
                    _boundary_penalty(words[end - 1].text) + suffix[1]
                    if end < len(words)
                    else suffix[1]
                )
                breaks = ((end - 1,) + suffix[2]) if end < len(words) else suffix[2]
                result = (variance, semantic_penalty, breaks)
                if best_result is None or result < best_result:
                    best_result = result
            return best_result

        result = best(0, line_count)
        return result[2] if result is not None else None

    return (
        find_layout(avoid_function_word_endings=True)
        or find_layout(avoid_function_word_endings=False)
        or ()
    )


def _is_function_word(text: str) -> bool:
    return text.casefold().strip(".,;:!?…¿¡\"'()[]{}«»") in _FUNCTION_WORDS


def _boundary_penalty(text: str) -> int:
    return 0 if text.rstrip().endswith((*_CLAUSE_ENDINGS, *_SENTENCE_ENDINGS)) else 1
