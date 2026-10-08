"""Registro central de estilos; los estilos describen comportamiento, no generan ASS."""

from app.styles.base import CaptionStyle


class StyleRegistry:
    def __init__(self) -> None:
        styles = (
            CaptionStyle("tiktok_pop", "TikTok Pop", "pop"),
            CaptionStyle("karaoke", "Karaoke", "karaoke"),
            CaptionStyle("one_word", "Una palabra", "one_word"),
            CaptionStyle("minimal", "Minimal", "minimal"),
            CaptionStyle("bold_highlight", "Resaltado", "highlight"),
        )
        self._styles = {style.style_id: style for style in styles}

    def get(self, style_id: str) -> CaptionStyle:
        try:
            return self._styles[style_id]
        except KeyError as exc:
            raise ValueError(f"Estilo desconocido: {style_id}") from exc

    def all(self) -> tuple[CaptionStyle, ...]:
        return tuple(self._styles.values())
