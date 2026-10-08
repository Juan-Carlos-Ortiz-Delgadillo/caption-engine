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
        caption_layouts: tuple[tuple[float, float, float, float], ...] | None = None,
    ) -> str:
        validate_captions(captions)
        if caption_layouts is not None and len(caption_layouts) != len(captions):
            raise ValueError("Debe proporcionarse una posición para cada caption.")
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
            (
                f"Style: Default,{_ass_text(config.font_family)},{config.font_size},"
                f"{_ass_color(config.primary_color)},{_ass_color(config.highlight_color)},"
                f"{_ass_color(config.outline_color)},{_ass_color(config.shadow_color)},"
                f"{-1 if style.behavior == 'highlight' else 0},0,0,0,100,100,0,0,1,"
                f"{config.outline_width:g},{config.shadow_depth:g},{alignment},50,50,"
                f"{margin_v},1"
            ),
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]
        events: list[str] = []
        for caption_index, caption in enumerate(captions):
            position = (
                _position_tag(caption_layouts[caption_index], width, height)
                if caption_layouts is not None
                else ""
            )
            if style.behavior == "karaoke" and highlight_enabled:
                text = _karaoke_text(caption.words, config)
                animation = fade_in(config.animation_duration_ms) if fade_enabled else ""
                text = position + animation + text
                events.append(_event(caption.start, caption.end, text))
            elif style.behavior == "one_word":
                for word in caption.words:
                    text = _format_word(word.text, config)
                    animation = pop(config.animation_duration_ms)
                    if fade_enabled:
                        animation += fade_in(config.animation_duration_ms)
                    text = position + animation + text
                    events.append(_event(word.start, word.end, text))
            elif not highlight_enabled:
                text = " ".join(_format_word(word.text, config) for word in caption.words)
                animation = pop(config.animation_duration_ms) if style.behavior == "pop" else ""
                if fade_enabled:
                    animation += fade_in(config.animation_duration_ms)
                events.append(_event(caption.start, caption.end, position + animation + text))
            else:
                for active_index, word in enumerate(caption.words):
                    prefix = pop(config.animation_duration_ms) if style.behavior == "pop" else ""
                    if fade_enabled:
                        prefix += fade_in(config.animation_duration_ms)
                    text = _highlighted_text(caption.words, active_index, config)
                    events.append(_event(word.start, word.end, position + prefix + text))
        return "\n".join((*header, *events, ""))


def _highlighted_text(words: tuple[Word, ...], active_index: int, config: CaptionStyleConfig) -> str:
    parts = []
    for index, word in enumerate(words):
        value = _format_word(word.text, config)
        if index == active_index:
            value = r"{\1c" + _ass_color(config.highlight_color) + "}" + value + r"{\r}"
        parts.append(value)
    return " ".join(parts)


def _karaoke_text(words: tuple[Word, ...], config: CaptionStyleConfig) -> str:
    parts = []
    for word in words:
        duration = max(1, round((word.end - word.start) * 100))
        parts.append(r"{\k" + str(duration) + "}" + _format_word(word.text, config))
    return " ".join(parts)


def _format_word(text: str, config: CaptionStyleConfig) -> str:
    text = text.upper() if config.uppercase else text
    return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")


def _event(start: float, end: float, text: str) -> str:
    return f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{text}"


def _ass_time(seconds: float) -> str:
    centiseconds = max(0, round(seconds * 100))
    hours, remainder = divmod(centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    whole_seconds, centiseconds = divmod(remainder, 100)
    return f"{hours}:{minutes:02}:{whole_seconds:02}.{centiseconds:02}"


def _ass_color(color: str) -> str:
    red, green, blue = color[1:3], color[3:5], color[5:7]
    return f"&H00{blue}{green}{red}".upper()


def _ass_text(value: str) -> str:
    return value.replace(",", " ")


def _position_tag(
    layout: tuple[float, float, float, float], width: int, height: int
) -> str:
    from math import isfinite

    if not all(isfinite(value) for value in layout):
        raise ValueError("El layout del caption debe contener valores finitos.")
    x, y, scale, spacing = layout
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
        + "}"
    )
