"""Modelos de dominio independientes de Qt y de las bibliotecas de infraestructura."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from math import isfinite
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class Word:
    text: str
    start: float
    end: float
    confidence: float | None = None

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("La palabra no puede estar vacía.")
        if (
            not isfinite(self.start)
            or not isfinite(self.end)
            or self.start < 0
            or self.end < self.start
            or self.confidence is not None
            and not isfinite(self.confidence)
        ):
            raise ValueError("Los tiempos de palabra no son válidos.")


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    start: float
    end: float
    words: tuple[Word, ...]

    def __post_init__(self) -> None:
        if self.start < 0 or self.end < self.start:
            raise ValueError("Los tiempos del segmento no son válidos.")


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    language: str
    segments: tuple[TranscriptSegment, ...]
    language_probability: float | None = None

    @property
    def words(self) -> tuple[Word, ...]:
        return tuple(word for segment in self.segments for word in segment.words)

    @property
    def duration(self) -> float:
        return max((word.end for word in self.words), default=0.0)


@dataclass(frozen=True, slots=True)
class TranscriptionOptions:
    model: str = "small"
    language: str | None = None
    device: str = "auto"
    compute_type: str = "int8"
    beam_size: int = 5
    vad_filter: bool = True


@dataclass(frozen=True, slots=True)
class Caption:
    start: float
    end: float
    words: tuple[Word, ...]

    def __post_init__(self) -> None:
        if not self.words:
            raise ValueError("Un subtítulo debe contener al menos una palabra.")
        if self.start < 0 or self.end < self.start:
            raise ValueError("Los tiempos del subtítulo no son válidos.")

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.words)


@dataclass(frozen=True, slots=True)
class SegmentationOptions:
    max_words: int = 7
    max_characters: int = 42
    max_duration: float = 3.5
    min_duration: float = 0.65
    max_lines: int = 2
    max_pause: float = 0.45


@dataclass(frozen=True, slots=True)
class CaptionStyleConfig:
    font_family: str = "Arial"
    font_size: int = 64
    primary_color: str = "#FFFFFF"
    highlight_color: str = "#FFE600"
    outline_color: str = "#000000"
    shadow_color: str = "#000000"
    background_color: str = "#000000"
    outline_width: float = 4.0
    shadow_depth: float = 1.5
    position: str = "bottom"
    margin_vertical: int = 70
    uppercase: bool = False
    animation_duration_ms: int = 140
    fade_enabled: bool | None = None
    highlight_enabled: bool | None = None

    def __post_init__(self) -> None:
        if not 8 <= self.font_size <= 240:
            raise ValueError("El tamaño de fuente debe estar entre 8 y 240.")
        if (
            not isfinite(self.outline_width)
            or not isfinite(self.shadow_depth)
            or self.outline_width < 0
            or self.shadow_depth < 0
            or self.margin_vertical < 0
            or self.animation_duration_ms < 0
        ):
            raise ValueError("La configuración visual contiene valores no válidos.")
        for color in (
            self.primary_color,
            self.highlight_color,
            self.outline_color,
            self.shadow_color,
            self.background_color,
        ):
            if len(color) != 7 or not color.startswith("#"):
                raise ValueError(f"Color hexadecimal no válido: {color}")
            try:
                int(color[1:], 16)
            except ValueError as exc:
                raise ValueError(f"Color hexadecimal no válido: {color}") from exc
        if self.position not in {"top", "center", "bottom"}:
            raise ValueError("La posición debe ser top, center o bottom.")
        if self.fade_enabled is not None and not isinstance(self.fade_enabled, bool):
            raise ValueError("La opción de fade debe ser booleana.")
        if self.highlight_enabled is not None and not isinstance(
            self.highlight_enabled, bool
        ):
            raise ValueError("La opción de resaltado debe ser booleana.")


@dataclass(frozen=True, slots=True)
class VideoMetadata:
    width: int
    height: int
    duration: float


@dataclass(frozen=True, slots=True)
class RenderOptions:
    source: Path
    subtitles: Path
    output: Path
    duration: float
    preset: str = "medium"
    crf: int = 20


@dataclass(frozen=True, slots=True)
class Project:
    source_video: Path
    transcription: TranscriptionOptions = field(default_factory=TranscriptionOptions)
    style_id: str = "tiktok_pop"
    style_config: CaptionStyleConfig = field(default_factory=CaptionStyleConfig)
    output_directory: Path | None = None


def result_to_dict(result: TranscriptionResult) -> dict[str, Any]:
    """Serializa el resultado con acceso directo a palabras y segmentos."""
    return {
        "language": result.language,
        "duration": result.duration,
        "language_probability": result.language_probability,
        "words": [asdict(word) for word in result.words],
        "segments": [asdict(segment) for segment in result.segments],
    }
