"""Flujo de aplicación de transcripción hasta renderizado y exportación."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from collections.abc import Callable

from app.ass.generator import ASSGenerator
from app.captions.segmenter import CaptionSegmenter
from app.core.exceptions import RenderingError
from app.core.models import (
    CaptionStyleConfig,
    RenderOptions,
    SegmentationOptions,
    TranscriptionOptions,
    result_to_dict,
)
from app.rendering.ffmpeg import FFmpegService
from app.rendering.renderer import VideoRenderer
from app.srt.generator import SRTGenerator
from app.transcription.base import TranscriptionEngine

logger = logging.getLogger(__name__)
PipelineProgress = Callable[[str, int], None]


@dataclass(frozen=True, slots=True)
class PipelineResult:
    output_directory: Path
    video: Path
    ass: Path
    srt: Path
    transcript: Path
    project: Path


class CaptionApplicationService:
    def __init__(
        self,
        transcription_engine: TranscriptionEngine,
        segmenter: CaptionSegmenter,
        ass_generator: ASSGenerator,
        srt_generator: SRTGenerator,
        ffmpeg: FFmpegService,
    ) -> None:
        self._transcription = transcription_engine
        self._segmenter = segmenter
        self._ass = ass_generator
        self._srt = srt_generator
        self._ffmpeg = ffmpeg

    def generate(
        self,
        source: Path,
        output_root: Path,
        transcription_options: TranscriptionOptions,
        style_id: str,
        style_config: CaptionStyleConfig,
        progress: PipelineProgress | None = None,
    ) -> PipelineResult:
        def report(message: str, percent: int) -> None:
            if progress:
                progress(message, percent)

        if not source.is_file():
            raise RenderingError(
                "No se pudo leer el video seleccionado. Vuelve a elegir el archivo."
            )
        self._ffmpeg.check()
        folder = _create_project_directory(output_root, _safe_name(source.stem))
        metadata = self._ffmpeg.metadata(source)
        report("Transcribiendo video en el equipo…", 5)

        def transcription_progress(message: str, percent: float | None) -> None:
            report(message, 10 if percent is None else 10 + round(44 * percent / 100))

        transcript = self._transcription.transcribe(
            source, transcription_options, transcription_progress
        )
        transcript_path = folder / "transcript.json"
        transcript_path.write_text(
            json.dumps(result_to_dict(transcript), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        project_path = folder / "project.acs"
        project_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "source_video": str(source),
                    "transcription": asdict(transcription_options),
                    "style_id": style_id,
                    "style_config": asdict(style_config),
                    "output_directory": str(folder),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        report("Organizando subtítulos…", 55)
        captions = self._segmenter.segment(transcript, SegmentationOptions())
        ass_path, srt_path = folder / "captions.ass", folder / "captions.srt"
        ass_path.write_text(
            self._ass.generate(
                captions, style_id, style_config, metadata.width, metadata.height
            ),
            encoding="utf-8",
        )
        srt_path.write_text(self._srt.generate(captions), encoding="utf-8")
        video_path = folder / "video_captioned.mp4"
        report("Renderizando video con FFmpeg…", 65)
        renderer = VideoRenderer(self._ffmpeg)
        renderer.render(
            RenderOptions(source, ass_path, video_path, metadata.duration),
            lambda ratio: report("Renderizando video con FFmpeg…", 65 + round(34 * ratio)),
        )
        report("Proceso completado.", 100)
        return PipelineResult(
            folder, video_path, ass_path, srt_path, transcript_path, project_path
        )


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^\w.-]+", "_", name, flags=re.UNICODE).strip("._")
    return cleaned[:80] or "proyecto"


def _create_project_directory(output_root: Path, project_name: str) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    for index in range(1, 10_000):
        suffix = "" if index == 1 else f"_{index}"
        candidate = output_root / f"{project_name}{suffix}"
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        return candidate
    raise FileExistsError("No hay nombres de carpeta de salida disponibles para este proyecto.")
