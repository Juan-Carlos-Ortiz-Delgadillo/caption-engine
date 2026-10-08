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
    ) -> str:
        validate_captions(captions)
        style = self._registry.get(style_id)
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
        for caption in captions:
            if style.behavior == "karaoke":
                text = _karaoke_text(caption.words, config)
                text = fade_in(config.animation_duration_ms) + text
                events.append(_event(caption.start, caption.end, text))
            elif style.behavior == "one_word":
                for word in caption.words:
                    text = _format_word(word.text, config)
                    text = pop(config.animation_duration_ms) + text
                    events.append(_event(word.start, word.end, text))
            else:
                for active_index, word in enumerate(caption.words):
                    prefix = pop(config.animation_duration_ms) if style.behavior == "pop" else ""
                    text = _highlighted_text(caption.words, active_index, config)
                    events.append(_event(word.start, word.end, prefix + text))
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
