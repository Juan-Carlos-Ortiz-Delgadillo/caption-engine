"""Generador ASS con resaltado temporizado por palabra."""

from app.ass.animations import fade_in, pop
from app.captions.validator import validate_captions
from app.core.models import Caption, CaptionStyleConfig, Word
from app.styles.registry import StyleRegistry


class ASSGenerator:
    def __init__(self, registry: StyleRegistry | None = None) -> None:
        self._registry = registry or StyleRegistry()

    def generate(
        self,
        captions: tuple[Caption, ...],
        style_id: str = "tiktok_pop",
        config: CaptionStyleConfig = CaptionStyleConfig(),
        width: int = 1920,
        height: int = 1080,
        caption_layouts: tuple[tuple[float, ...], ...] | None = None,
        caption_font_sizes: tuple[int, ...] | None = None,
    ) -> str:
        validate_captions(captions)
        if caption_layouts is not None and len(caption_layouts) != len(captions):
            raise ValueError("Debe proporcionarse una posición para cada caption.")
        if caption_font_sizes is not None and (
            len(caption_font_sizes) != len(captions)
            or any(not 8 <= font_size <= config.font_size for font_size in caption_font_sizes)
        ):
            raise ValueError("Debe proporcionarse un tamaño de fuente válido para cada caption.")
        style = self._registry.get(style_id)
        highlight_enabled = (
            config.highlight_enabled
            if config.highlight_enabled is not None
            else style.behavior != "one_word"
        )
        fade_enabled = (
            config.fade_enabled
            if config.fade_enabled is not None
            else style.behavior == "karaoke"
        )
        alignment = {"bottom": 2, "center": 5, "top": 8}[config.position]
        margin_v = config.margin_vertical
        outline_width = (
            max(config.outline_width, config.bubble_padding)
            if config.bubble_enabled
            else config.outline_width
        )
        default_style = (
            f"Style: Default,{_ass_text(config.font_family)},{config.font_size},"
            f"{_ass_color(config.primary_color)},{_ass_color(config.highlight_color)},"
            f"{_ass_color(config.outline_color)},"
            f"{_ass_color(config.background_color, 100 - config.bubble_opacity)},"
            f"{-1 if config.bold or style.behavior == 'highlight' else 0},"
            f"{-1 if config.italic else 0},{-1 if config.underline else 0},0,100,100,"
            f"0,0,{3 if config.bubble_enabled else 1},{outline_width:g},"
            f"{config.shadow_depth:g},{alignment},50,50,"
            f"{margin_v},1"
        )
        fit_styles = []
        if caption_font_sizes is not None:
            style_properties = default_style.split(",", 3)[3]
            fit_styles = [
                f"Style: CaptionFit{font_size},{_ass_text(config.font_family)},{font_size},"
                f"{style_properties}"
                for font_size in sorted(set(caption_font_sizes))
                if font_size != config.font_size
            ]
        header = [
            "[Script Info]",
            "ScriptType: v4.00+",
            "WrapStyle: 2",
            "ScaledBorderAndShadow: yes",
            f"PlayResX: {max(1, width)}",
            f"PlayResY: {max(1, height)}",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
            "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
            "MarginL, MarginR, MarginV, Encoding",
            default_style,
            *fit_styles,
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]
        events: list[str] = []
        for caption_index, caption in enumerate(captions):
            layout = caption_layouts[caption_index] if caption_layouts is not None else None
            position = _position_tag(layout, width, height) if layout is not None else ""
            font_size = (
                caption_font_sizes[caption_index]
                if caption_font_sizes is not None
                else config.font_size
            )
            style_name = "Default" if font_size == config.font_size else f"CaptionFit{font_size}"
            effects = _effect_tag(config)
            lines = _word_lines(caption.words, caption.line_breaks)
            if len(lines) > 1 and config.line_spacing > 0 and layout is not None:
                base_x, base_y, scale, spacing = layout[:4]
                rotation = layout[4] if len(layout) == 5 else 0.0
                line_step = (config.font_size * scale + config.line_spacing) / max(1, height)
                positions = tuple(
                    _position_tag(
                        (
                            base_x,
                            max(
                                0.0,
                                min(
                                    1.0,
                                    base_y
                                    + (line_index - (len(lines) - 1) / 2)
                                    * line_step,
                                ),
                            ),
                            scale,
                            spacing,
                            rotation,
                        ),
                        width,
                        height,
                    )
                    for line_index in range(len(lines))
                )
                _append_separate_line_events(
                    events,
                    lines,
                    positions,
                    caption,
                    style.behavior,
                    highlight_enabled,
                    fade_enabled,
                    effects,
                    config,
                    style_name,
                )
                continue
            if style.behavior == "karaoke" and highlight_enabled:
                text = _karaoke_text(caption.words, config, caption.line_breaks)
                animation = fade_in(config.animation_duration_ms) if fade_enabled else ""
                text = position + effects + animation + text
                events.append(_event(caption.start, caption.end, text, style_name))
            elif style.behavior == "one_word":
                for word in caption.words:
                    text = _format_word(word.text, config)
                    animation = pop(config.animation_duration_ms)
                    if fade_enabled:
                        animation += fade_in(config.animation_duration_ms)
                    text = position + effects + animation + text
                    events.append(_event(word.start, word.end, text, style_name))
            elif not highlight_enabled:
                text = _join_words(
                    tuple(_format_word(word.text, config) for word in caption.words),
                    caption.line_breaks,
                )
                animation = pop(config.animation_duration_ms) if style.behavior == "pop" else ""
                if fade_enabled:
                    animation += fade_in(config.animation_duration_ms)
                events.append(
                    _event(
                        caption.start,
                        caption.end,
                        position + effects + animation + text,
                        style_name,
                    )
                )
            else:
                for active_index, word in enumerate(caption.words):
                    prefix = pop(config.animation_duration_ms) if style.behavior == "pop" else ""
                    if fade_enabled:
                        prefix += fade_in(config.animation_duration_ms)
                    text = _highlighted_text(
                        caption.words, active_index, config, caption.line_breaks
                    )
                    events.append(
                        _event(
                            word.start,
                            word.end,
                            position + effects + prefix + text,
                            style_name,
                        )
                    )
        return "\n".join((*header, *events, ""))


def _append_separate_line_events(
    events: list[str],
    lines: tuple[tuple[int, tuple[Word, ...]], ...],
    positions: tuple[str, ...],
    caption: Caption,
    behavior: str,
    highlight_enabled: bool,
    fade_enabled: bool,
    effects: str,
    config: CaptionStyleConfig,
    style_name: str,
) -> None:
    if behavior == "karaoke" and highlight_enabled:
        for (start_index, words), position in zip(lines, positions):
            text = _karaoke_text(words, config)
            animation = fade_in(config.animation_duration_ms) if fade_enabled else ""
            events.append(
                _event(
                    caption.start,
                    caption.end,
                    position + effects + animation + text,
                    style_name,
                )
            )
    elif behavior == "one_word":
        for (start_index, words), position in zip(lines, positions):
            for word in words:
                animation = pop(config.animation_duration_ms)
                if fade_enabled:
                    animation += fade_in(config.animation_duration_ms)
                events.append(
                    _event(
                        word.start,
                        word.end,
                        position + effects + animation + _format_word(word.text, config),
                        style_name,
                    )
                )
    elif not highlight_enabled:
        for (_, words), position in zip(lines, positions):
            text = " ".join(_format_word(word.text, config) for word in words)
            animation = pop(config.animation_duration_ms) if behavior == "pop" else ""
            if fade_enabled:
                animation += fade_in(config.animation_duration_ms)
            events.append(
                _event(
                    caption.start,
                    caption.end,
                    position + effects + animation + text,
                    style_name,
                )
            )
    else:
        for active_index, active_word in enumerate(caption.words):
            for (start_index, words), position in zip(lines, positions):
                local_index = active_index - start_index
                line_active_index = (
                    local_index if 0 <= local_index < len(words) else None
                )
                text = _highlighted_text(words, line_active_index, config)
                prefix = pop(config.animation_duration_ms) if behavior == "pop" else ""
                if fade_enabled:
                    prefix += fade_in(config.animation_duration_ms)
                events.append(
                    _event(
                        active_word.start,
                        active_word.end,
                        position + effects + prefix + text,
                        style_name,
                    )
                )


def _word_lines(
    words: tuple[Word, ...], line_breaks: tuple[int, ...]
) -> tuple[tuple[int, tuple[Word, ...]], ...]:
    lines = []
    start = 0
    for break_after in (*line_breaks, len(words) - 1):
        end = break_after + 1
        lines.append((start, words[start:end]))
        start = end
    return tuple(lines)


def _highlighted_text(
    words: tuple[Word, ...],
    active_index: int | None,
    config: CaptionStyleConfig,
    line_breaks: tuple[int, ...] = (),
) -> str:
    parts = []
    for index, word in enumerate(words):
        value = _format_word(word.text, config)
        if index == active_index:
            value = r"{\1c" + _ass_color(config.highlight_color) + "}" + value + r"{\r}"
        parts.append(value)
    return _join_words(tuple(parts), line_breaks)


def _karaoke_text(
    words: tuple[Word, ...],
    config: CaptionStyleConfig,
    line_breaks: tuple[int, ...] = (),
) -> str:
    parts = []
    for word in words:
        duration = max(1, round((word.end - word.start) * 100))
        parts.append(r"{\k" + str(duration) + "}" + _format_word(word.text, config))
    return _join_words(tuple(parts), line_breaks)


def _join_words(words: tuple[str, ...], line_breaks: tuple[int, ...]) -> str:
    parts = []
    for index, word in enumerate(words):
        if index:
            parts.append(r"\N" if index - 1 in line_breaks else " ")
        parts.append(word)
    return "".join(parts)


def _format_word(text: str, config: CaptionStyleConfig) -> str:
    if config.uppercase or config.case_mode == "upper":
        text = text.upper()
    elif config.case_mode == "lower":
        text = text.lower()
    elif config.case_mode == "capitalize":
        text = text.title()
    return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")


def _event(start: float, end: float, text: str, style_name: str = "Default") -> str:
    return f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},{style_name},,0,0,0,,{text}"


def _ass_time(seconds: float) -> str:
    centiseconds = max(0, round(seconds * 100))
    hours, remainder = divmod(centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    whole_seconds, centiseconds = divmod(remainder, 100)
    return f"{hours}:{minutes:02}:{whole_seconds:02}.{centiseconds:02}"


def _ass_color(color: str, alpha: int = 0) -> str:
    red, green, blue = color[1:3], color[3:5], color[5:7]
    alpha_byte = max(0, min(255, round(alpha * 255 / 100)))
    return f"&H{alpha_byte:02X}{blue}{green}{red}".upper()


def _ass_text(value: str) -> str:
    return value.replace(",", " ")


def _position_tag(
    layout: tuple[float, ...], width: int, height: int
) -> str:
    from math import isfinite

    if not all(isfinite(value) for value in layout):
        raise ValueError("El layout del caption debe contener valores finitos.")
    if len(layout) not in {4, 5}:
        raise ValueError("El layout requiere posición, escala, espaciado y rotación opcional.")
    x, y, scale, spacing = layout[:4]
    rotation = layout[4] if len(layout) == 5 else 0.0
    if not 0 <= x <= 1 or not 0 <= y <= 1 or not 0.25 <= scale <= 4:
        raise ValueError("La posición o escala del caption no es válida.")
    return (
        r"{\an5\pos("
        + f"{round(x * max(1, width))},{round(y * max(1, height))}"
        + r")\fscx"
        + f"{round(scale * 100)}"
        + r"\fscy"
        + f"{round(scale * 100)}"
        + r"\fsp"
        + f"{spacing:g}"
        + (r"\frz" + f"{rotation:g}" if rotation else "")
        + "}"
    )


def _effect_tag(config: CaptionStyleConfig) -> str:
    blur = max(config.blur, config.glow_radius if config.glow_enabled else 0.0)
    return r"{\blur" + f"{blur:g}" + "}" if blur > 0 else ""
