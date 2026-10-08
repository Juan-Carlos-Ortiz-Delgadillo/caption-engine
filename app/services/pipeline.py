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
from app.editor.models import CaptionEditorProject
from app.core.models import (
    Caption,
    CaptionStyleConfig,
    RenderOptions,
    SegmentationOptions,
    TranscriptionResult,
    TranscriptionOptions,
    VideoMetadata,
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
    source_video: Path
    captions: tuple[Caption, ...]
    transcription: TranscriptionResult
    metadata: VideoMetadata
    style_id: str
    style_config: CaptionStyleConfig
    transcription_options: TranscriptionOptions


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

    def video_metadata(self, source: Path) -> VideoMetadata:
        if not source.is_file():
            raise RenderingError("No se encontró el video fuente del proyecto.")
        return self._ffmpeg.metadata(source)

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
            folder,
            video_path,
            ass_path,
            srt_path,
            transcript_path,
            project_path,
            source,
            captions,
            transcript,
            metadata,
            style_id,
            style_config,
            transcription_options,
        )

    def export_editor_project(
        self,
        project: CaptionEditorProject,
        metadata: VideoMetadata,
        output_directory: Path,
        render_video: bool = True,
        progress: PipelineProgress | None = None,
    ) -> PipelineResult:
        def report(message: str, percent: int) -> None:
            if progress:
                progress(message, percent)

        if not project.source_video.is_file():
            raise RenderingError("No se pudo leer el video fuente del proyecto.")
        output_directory.mkdir(parents=True, exist_ok=True)
        captions = tuple(caption.to_caption() for caption in project.captions)
        layouts = tuple(
            (caption.x, caption.y, caption.scale, caption.word_spacing)
            for caption in project.captions
        )
        ass_path = output_directory / "captions.ass"
        srt_path = output_directory / "captions.srt"
        transcript_path = output_directory / "transcript.json"
        project_path = output_directory / "project.acs"
        video_path = output_directory / "video_captioned.mp4"
        ass_path.write_text(
            self._ass.generate(
                captions,
                project.style_id,
                project.style_config,
                metadata.width,
                metadata.height,
                caption_layouts=layouts,
            ),
            encoding="utf-8",
        )
        srt_path.write_text(self._srt.generate(captions), encoding="utf-8")
        report("ASS y SRT exportados.", 40)
        if render_video:
            self._ffmpeg.check()
            renderer = VideoRenderer(self._ffmpeg)
            renderer.render(
                RenderOptions(
                    project.source_video,
                    ass_path,
                    video_path,
                    metadata.duration,
                ),
                lambda ratio: report(
                    "Renderizando video con los cambios…", 40 + round(59 * ratio)
                ),
            )
        report("Exportación completada.", 100)
        return PipelineResult(
            output_directory=output_directory,
            video=video_path,
            ass=ass_path,
            srt=srt_path,
            transcript=transcript_path,
            project=project_path,
            source_video=project.source_video,
            captions=captions,
            transcription=project.original_transcript,
            metadata=metadata,
            style_id=project.style_id,
            style_config=project.style_config,
            transcription_options=project.transcription_options,
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
