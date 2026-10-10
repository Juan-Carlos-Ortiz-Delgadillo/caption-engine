"""Pixel-based text balancing for caption overlays."""

from __future__ import annotations

import logging
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import cache, lru_cache
from math import isfinite

from PySide6.QtGui import QFont, QFontDatabase, QFontInfo, QFontMetricsF

from app.core.exceptions import CaptionLayoutError
from app.core.models import Caption, CaptionStyleConfig, Word

logger = logging.getLogger(__name__)

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
_TRAILING_PUNCTUATION = ".,;:!?…¿¡\"'()[]{}«»"


@dataclass(frozen=True, slots=True)
class TextLayout:
    lines: tuple[str, ...]
    font_size: int
    line_height: float
    estimated_width: float
    estimated_height: float
    overflow: bool
    failure_reason: str | None = None


def layout_captions(
    captions: tuple[Caption, ...],
    config: CaptionStyleConfig,
    video_width: int,
    video_height: int,
    *,
    bold_style: bool = False,
) -> tuple[tuple[Caption, ...], tuple[int, ...]]:
    """Fit generated captions to video dimensions and preserve word boundaries."""
    if video_width < 1 or video_height < 1:
        raise ValueError("Las dimensiones del video deben ser positivas.")

    side_margin = 80 * video_width / 1080
    max_width = video_width - 2 * side_margin
    max_height = video_height - 2 * config.margin_vertical
    if max_width <= 0 or max_height <= 0:
        raise CaptionLayoutError(
            "La resolución del video no deja espacio para los márgenes configurados."
        )
    effect_margin = config.shadow_depth + 3 * max(
        config.blur, config.glow_radius if config.glow_enabled else 0.0
    )
    stroke_width = (
        max(config.outline_width, config.bubble_padding)
        if config.bubble_enabled
        else config.outline_width
    )
    minimum_font_size = min(config.font_size, 56)
    font_weight = 700 if config.bold or bold_style else 400
    laid_out: list[Caption] = []
    font_sizes: list[int] = []

    for caption in captions:
        caption_words = caption.words
        words = tuple(_display_word(word.text, config) for word in caption_words)
        layout = balance_text(
            " ".join(words),
            config.font_family,
            font_weight,
            config.font_size,
            minimum_font_size,
            max_width,
            max_height,
            2,
            config.font_size * 1.2 + config.line_spacing,
            stroke_width,
            0.0,
            effect_margin=effect_margin,
            italic=config.italic,
        )
        if layout.overflow:
            fallback_words = _split_oversized_words(
                caption_words,
                config.font_family,
                font_weight,
                minimum_font_size,
                max_width,
                stroke_width,
                effect_margin,
                config.italic,
                lambda text: _display_word(text, config),
            )
            if fallback_words is not None:
                display_words = tuple(
                    _display_word(word.text, config) for word in fallback_words
                )
                layout = balance_text(
                    " ".join(display_words),
                    config.font_family,
                    font_weight,
                    minimum_font_size,
                    minimum_font_size,
                    max_width,
                    max_height,
                    min(len(display_words), 6),
                    minimum_font_size * 1.2 + config.line_spacing,
                    stroke_width,
                    0.0,
                    effect_margin=effect_margin,
                    italic=config.italic,
                )
                if not layout.overflow:
                    caption_words = fallback_words
                    words = display_words
                    logger.warning(
                        "Se aplicó el ajuste de emergencia al caption %d: "
                        "%d línea(s), fuente de %d px%s.",
                        len(laid_out) + 1,
                        len(layout.lines),
                        layout.font_size,
                        " y división con guion" if fallback_words != caption.words else "",
                    )
        if layout.overflow:
            raise CaptionLayoutError(
                f"No se pudo ajustar el caption «{caption.text}»: {layout.failure_reason}"
            )

        line_breaks: list[int] = []
        word_index = 0
        for line in layout.lines[:-1]:
            word_index += len(line.split())
            line_breaks.append(word_index - 1)
        laid_out.append(
            replace(
                caption,
                words=caption_words,
                line_breaks=tuple(line_breaks),
            )
        )
        font_sizes.append(layout.font_size)

    return tuple(laid_out), tuple(font_sizes)


def fit_manual_caption(
    caption: Caption,
    config: CaptionStyleConfig,
    video_width: int,
    video_height: int,
    *,
    bold_style: bool = False,
) -> tuple[int | None, str | None]:
    """Fit a caption while keeping its user-authored line boundaries."""
    side_margin = 80 * video_width / 1080
    max_width = video_width - 2 * side_margin
    max_height = video_height - 2 * config.margin_vertical
    if max_width <= 0 or max_height <= 0:
        return None, "La resolución no deja espacio para los márgenes configurados."

    lines: list[str] = []
    start = 0
    for break_after in (*caption.line_breaks, len(caption.words) - 1):
        lines.append(
            " ".join(
                _display_word(word.text, config)
                for word in caption.words[start : break_after + 1]
            )
        )
        start = break_after + 1
    if len(lines) > 6:
        return None, "Las líneas manuales exceden el máximo de seis líneas."

    effect_margin = config.shadow_depth + 3 * max(
        config.blur, config.glow_radius if config.glow_enabled else 0.0
    )
    stroke_width = (
        max(config.outline_width, config.bubble_padding)
        if config.bubble_enabled
        else config.outline_width
    )
    font_weight = 700 if config.bold or bold_style else 400
    minimum_font_size = min(config.font_size, 56)
    for font_size in range(config.font_size, minimum_font_size - 1, -1):
        font = QFont(config.font_family)
        font.setPixelSize(font_size)
        font.setWeight(QFont.Weight(font_weight))
        font.setItalic(config.italic)
        font_info = QFontInfo(font)
        if font_info.family().casefold() != config.font_family.casefold():
            return None, (
                f"La fuente «{config.font_family}» no está instalada o Qt usa "
                "una fuente alternativa."
            )
        metrics = QFontMetricsF(font)
        effect_width = 2 * (stroke_width + effect_margin)
        width = max(
            (metrics.horizontalAdvance(line) + effect_width for line in lines),
            default=0.0,
        )
        line_height = max(
            (config.font_size * 1.2 + config.line_spacing)
            * font_size
            / config.font_size,
            metrics.lineSpacing(),
        )
        height = len(lines) * line_height + effect_width
        if width <= max_width and height <= max_height:
            return font_size, None
    return None, "Las líneas manuales no caben en el área segura del video."


def balance_text(
    text: str,
    font_family: str,
    font_weight: int,
    base_font_size: int,
    min_font_size: int,
    max_width: float,
    max_height: float,
    max_lines: int,
    line_height: float,
    stroke_width: float,
    letter_spacing: float,
    *,
    effect_margin: float = 0.0,
    function_word_penalty: float = 80.0,
    orphan_line_penalty: float = 120.0,
    italic: bool = False,
) -> TextLayout:
    """Balance whole words using the selected system font's actual pixel metrics."""
    _validate_options(
        font_family,
        font_weight,
        base_font_size,
        min_font_size,
        max_width,
        max_height,
        max_lines,
        line_height,
        stroke_width,
        letter_spacing,
        effect_margin,
        function_word_penalty,
        orphan_line_penalty,
    )
    words = tuple(text.split())
    if not words:
        return _failed_layout(base_font_size, "El texto está vacío.")

    if font_family.casefold() not in _font_families():
        return _failed_layout(
            base_font_size,
            f"La fuente «{font_family}» no está instalada; no se midió con "
            "una fuente alternativa.",
        )

    for font_size in range(base_font_size, min_font_size - 1, -1):
        font = QFont(font_family)
        font.setPixelSize(font_size)
        font.setWeight(QFont.Weight(font_weight))
        font.setItalic(italic)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, letter_spacing)
        font_info = QFontInfo(font)
        if font_info.family().casefold() != font_family.casefold():
            return _failed_layout(
                base_font_size,
                f"Qt resolvió «{font_family}» como «{font_info.family()}»; "
                "no se medirá con fallback.",
            )

        metrics = QFontMetricsF(font)

        def measured_width(value: str) -> float:
            return metrics.horizontalAdvance(value) + 2 * (stroke_width + effect_margin)

        actual_line_height = max(
            line_height * font_size / base_font_size,
            metrics.lineSpacing(),
        )
        for line_count in range(1, min(max_lines, len(words)) + 1):
            candidate = _best_split(
                words,
                line_count,
                measured_width,
                stroke_width,
                effect_margin,
                max_width,
                function_word_penalty,
                orphan_line_penalty,
            )
            if candidate is None:
                continue

            lines, line_widths = candidate
            height = len(lines) * actual_line_height + 2 * (stroke_width + effect_margin)
            width = max(line_widths, default=0.0)
            if width <= max_width and height <= max_height:
                return TextLayout(
                    lines,
                    font_size,
                    actual_line_height,
                    width,
                    height,
                    False,
                )

    return _failed_layout(
        min_font_size,
        _failure_reason(
            words,
            font_family,
            font_weight,
            min_font_size,
            max_width,
            max_height,
            max_lines,
            stroke_width,
            effect_margin,
            italic,
        ),
    )


def _best_split(
    words: tuple[str, ...],
    line_count: int,
    measure: Callable[[str], float],
    stroke_width: float,
    effect_margin: float,
    max_width: float,
    function_word_penalty: float,
    orphan_line_penalty: float,
) -> tuple[tuple[str, ...], tuple[float, ...]] | None:
    total_width = measure(" ".join(words)) - 2 * (stroke_width + effect_margin)
    target_width = total_width / line_count

    @cache
    def best(
        start: int, remaining: int
    ) -> tuple[float, tuple[str, ...], tuple[float, ...]] | None:
        words_left = len(words) - start
        if words_left < remaining:
            return None
        if remaining == 1:
            line = " ".join(words[start:])
            width = measure(line)
            if width > max_width:
                return None
            score = (width - target_width) ** 2
            if len(words[start:]) == 1 and width < target_width * 0.4:
                score += orphan_line_penalty
            return score, (line,), (width,)

        best_result = None
        last_end = len(words) - remaining + 1
        for end in range(start + 1, last_end + 1):
            line = " ".join(words[start:end])
            width = measure(line)
            if width > max_width:
                continue
            suffix = best(end, remaining - 1)
            if suffix is None:
                continue
            score = (width - target_width) ** 2 + suffix[0]
            if _is_function_word(words[end - 1]):
                score += function_word_penalty
            candidate = (score, (line, *suffix[1]), (width, *suffix[2]))
            if best_result is None or candidate[0] < best_result[0]:
                best_result = candidate
        return best_result

    result = best(0, line_count)
    return (result[1], result[2]) if result is not None else None


def _is_function_word(word: str) -> bool:
    return word.casefold().strip(_TRAILING_PUNCTUATION) in _FUNCTION_WORDS


@lru_cache(maxsize=1)
def _font_families() -> frozenset[str]:
    return frozenset(family.casefold() for family in QFontDatabase.families())


def _split_oversized_words(
    words: tuple[Word, ...],
    font_family: str,
    font_weight: int,
    font_size: int,
    max_width: float,
    stroke_width: float,
    effect_margin: float,
    italic: bool,
    display_text: Callable[[str], str],
) -> tuple[Word, ...] | None:
    font = QFont(font_family)
    font.setPixelSize(font_size)
    font.setWeight(QFont.Weight(font_weight))
    font.setItalic(italic)
    metrics = QFontMetricsF(font)

    def measured_width(text: str) -> float:
        return (
            metrics.horizontalAdvance(display_text(text))
            + 2 * (stroke_width + effect_margin)
        )

    result: list[Word] = []
    for word in words:
        if measured_width(word.text) <= max_width:
            result.append(word)
            continue
        chunks = _hyphenate_to_fit(word.text, measured_width, max_width)
        if chunks is None:
            return None
        content_lengths = tuple(len(chunk.rstrip("-")) for chunk in chunks)
        total_length = sum(content_lengths)
        duration = word.end - word.start
        elapsed_length = 0
        for index, (chunk, content_length) in enumerate(zip(chunks, content_lengths)):
            start = word.start + duration * elapsed_length / total_length
            elapsed_length += content_length
            end = (
                word.end
                if index == len(chunks) - 1
                else word.start + duration * elapsed_length / total_length
            )
            result.append(Word(chunk, start, end, word.confidence))
    return tuple(result)


def _hyphenate_to_fit(
    text: str,
    measured_width: Callable[[str], float],
    max_width: float,
) -> tuple[str, ...] | None:
    units: list[str] = []
    for character in text:
        if units and _is_combining_mark(character):
            units[-1] += character
        else:
            units.append(character)

    chunks: list[str] = []
    current = ""
    for index, unit in enumerate(units):
        candidate = current + unit
        suffix = "-" if index < len(units) - 1 else ""
        if measured_width(candidate + suffix) <= max_width:
            current = candidate
            continue
        if not current or measured_width(current + "-") > max_width:
            return None
        chunks.append(current + "-")
        current = unit
        if index < len(units) - 1 and measured_width(current + "-") > max_width:
            return None
        if index == len(units) - 1 and measured_width(current) > max_width:
            return None
    if current:
        chunks.append(current)
    return tuple(chunks) if len(chunks) > 1 else None


def _is_combining_mark(character: str) -> bool:
    return unicodedata.category(character).startswith("M")


def _failure_reason(
    words: tuple[str, ...],
    font_family: str,
    font_weight: int,
    font_size: int,
    max_width: float,
    max_height: float,
    max_lines: int,
    stroke_width: float,
    effect_margin: float,
    italic: bool,
) -> str:
    font = QFont(font_family)
    font.setPixelSize(font_size)
    font.setWeight(QFont.Weight(font_weight))
    font.setItalic(italic)
    metrics = QFontMetricsF(font)
    widest_word = max(
        words,
        key=lambda word: metrics.horizontalAdvance(word)
        + 2 * (stroke_width + effect_margin),
    )
    widest_width = (
        metrics.horizontalAdvance(widest_word)
        + 2 * (stroke_width + effect_margin)
    )
    if widest_width > max_width:
        return (
            f"La palabra «{widest_word}» mide {widest_width:.1f} px a {font_size} px "
            f"y supera el ancho máximo de {max_width:.1f} px."
        )
    return (
        "No hay una distribución de palabras completas que quepa en las dimensiones "
        f"asignadas (máximo {max_width:.1f} × {max_height:.1f} px, "
        f"{max_lines} líneas y fuente mínima de {font_size} px)."
    )


def _display_word(text: str, config: CaptionStyleConfig) -> str:
    if config.uppercase or config.case_mode == "upper":
        return text.upper()
    if config.case_mode == "lower":
        return text.lower()
    if config.case_mode == "capitalize":
        return text.title()
    return text


def _validate_options(
    font_family: str,
    font_weight: int,
    base_font_size: int,
    min_font_size: int,
    max_width: float,
    max_height: float,
    max_lines: int,
    line_height: float,
    stroke_width: float,
    letter_spacing: float,
    effect_margin: float,
    function_word_penalty: float,
    orphan_line_penalty: float,
) -> None:
    if not font_family.strip():
        raise ValueError("La familia tipográfica no puede estar vacía.")
    if not 1 <= font_weight <= 1000:
        raise ValueError("El peso de fuente debe estar entre 1 y 1000.")
    if base_font_size < 1 or min_font_size < 1 or min_font_size > base_font_size:
        raise ValueError("Los tamaños de fuente deben ser positivos y mínimo <= base.")
    if max_lines < 1:
        raise ValueError("El máximo de líneas debe ser positivo.")
    values = (
        max_width,
        max_height,
        line_height,
        stroke_width,
        letter_spacing,
        effect_margin,
        function_word_penalty,
        orphan_line_penalty,
    )
    if not all(isfinite(value) for value in values):
        raise ValueError("Las dimensiones y los efectos deben ser valores finitos.")
    if (
        max_width <= 0
        or max_height <= 0
        or line_height <= 0
        or stroke_width < 0
        or effect_margin < 0
        or function_word_penalty < 0
        or orphan_line_penalty < 0
    ):
        raise ValueError("Las dimensiones deben ser positivas y los márgenes no negativos.")


def _failed_layout(font_size: int, reason: str) -> TextLayout:
    return TextLayout((), font_size, 0.0, 0.0, 0.0, True, reason)
