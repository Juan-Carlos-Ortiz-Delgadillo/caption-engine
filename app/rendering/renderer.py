"""Orquestación de salida de video."""

from pathlib import Path

from app.core.models import RenderOptions
from app.rendering.ffmpeg import FFmpegService, ProgressCallback


class VideoRenderer:
    def __init__(self, ffmpeg: FFmpegService) -> None:
        self._ffmpeg = ffmpeg

    def render(
        self, options: RenderOptions, progress: ProgressCallback | None = None
    ) -> Path:
        self._ffmpeg.render(
            options.source, options.subtitles, options.output, options.duration, progress
        )
        return options.output
