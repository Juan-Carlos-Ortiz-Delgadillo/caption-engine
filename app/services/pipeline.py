"""Flujo de aplicación de transcripción hasta renderizado y exportación."""

from __future__ import annotations

import json
import logging
import re
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from app.ass.generator import ASSGenerator
from app.captions.balance_text import layout_captions
from app.captions.segmenter import CaptionSegmenter
from app.core.exceptions import RenderingError
from app.core.models import (
    Caption,
    CaptionStyleConfig,
    RenderOptions,
    SegmentationOptions,
    TranscriptionOptions,
    TranscriptionResult,
    VideoMetadata,
    result_to_dict,
)
from app.editor.layout import relayout_project
from app.editor.models import CaptionEditorProject, transcript_from_dict
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

    def reusable_transcripts(
        self,
        source: Path,
        output_root: Path,
        recent_projects: tuple[Path, ...] = (),
    ) -> tuple[Path, ...]:
        """Find saved transcripts for this exact source path, newest first."""
        candidates: set[Path] = set()
        project_paths = set(recent_projects)
        if output_root.is_dir():
            project_paths.update(output_root.glob("*/project.acs"))
        source_path = str(source.resolve())
        for project_path in project_paths:
            try:
                data = json.loads(project_path.read_text(encoding="utf-8"))
                project_source = Path(data["source_video"]).expanduser()
                transcript_path = project_path.with_name("transcript.json")
                if (
                    str(project_source.resolve()) == source_path
                    and transcript_path.is_file()
                ):
                    candidates.add(transcript_path)
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                logger.warning("No se pudo comprobar el proyecto reciente %s", project_path)
        return tuple(
            sorted(candidates, key=lambda path: path.stat().st_mtime, reverse=True)
        )

    def transcribe_to_workspace(
        self,
        source: Path,
        output_directory: Path,
        transcription_options: TranscriptionOptions,
        reuse_transcript: Path | None = None,
        progress: PipelineProgress | None = None,
    ) -> tuple[TranscriptionResult, VideoMetadata, Path]:
        if not source.is_file():
            raise RenderingError(
                "No se pudo leer el video seleccionado. Vuelve a elegir el archivo."
            )
        metadata = self._ffmpeg.metadata(source)
        output_directory.mkdir(parents=True, exist_ok=True)

        def report(message: str, percent: int) -> None:
            if progress:
                progress(message, percent)

        if reuse_transcript is not None:
            report("Reutilizando transcript.json guardado…", 20)
            transcript = self.load_transcript(reuse_transcript)
        else:
            report("Transcribiendo video en el equipo…", 5)

            def transcription_progress(message: str, percent: float | None) -> None:
                report(message, 10 if percent is None else 10 + round(85 * percent / 100))

            transcript = self._transcription.transcribe(
                source, transcription_options, transcription_progress
            )
        transcript_path = self.save_transcript(transcript, output_directory)
        report("Transcripción guardada.", 100)
        return transcript, metadata, transcript_path

    @staticmethod
    def create_workspace(output_root: Path, source: Path) -> Path:
        return _create_project_directory(output_root, _safe_name(source.stem))

    @staticmethod
    def save_transcript(
        transcript: TranscriptionResult, output_directory: Path
    ) -> Path:
        transcript_path = output_directory / "transcript.json"
        _write_json_atomic(transcript_path, result_to_dict(transcript))
        return transcript_path

    @staticmethod
    def load_transcript(path: Path) -> TranscriptionResult:
        data = json.loads(path.read_text(encoding="utf-8"))
        return transcript_from_dict(data)

    def build_editor_project(
        self,
        source: Path,
        transcript: TranscriptionResult,
        transcription_options: TranscriptionOptions,
        style_id: str,
        style_config: CaptionStyleConfig,
        metadata: VideoMetadata,
        progress: PipelineProgress | None = None,
    ) -> CaptionEditorProject:
        if progress:
            progress("Segmentando captions y aplicando layout…", 15)
        captions = self._segmenter.segment(transcript, SegmentationOptions())
        project = CaptionEditorProject.create(
            source,
            transcript,
            captions,
            style_id,
            style_config,
            transcription_options,
            metadata,
        )
        project = relayout_project(project)
        if progress:
            progress("Proyecto listo para editar.", 100)
        return project

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
        captions, caption_font_sizes = layout_captions(
            captions,
            style_config,
            metadata.width,
            metadata.height,
            bold_style=style_id == "bold_highlight",
        )
        ass_path, srt_path = folder / "captions.ass", folder / "captions.srt"
        ass_path.write_text(
            self._ass.generate(
                captions,
                style_id,
                style_config,
                metadata.width,
                metadata.height,
                caption_font_sizes=caption_font_sizes,
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
        *,
        output_name: str = "video_captioned.mp4",
        crf: int = 20,
    ) -> PipelineResult:
        def report(message: str, percent: int) -> None:
            if progress:
                progress(message, percent)

        if not project.source_video.is_file():
            raise RenderingError("No se pudo leer el video fuente del proyecto.")
        output_directory.mkdir(parents=True, exist_ok=True)
        captions = tuple(caption.to_caption() for caption in project.captions)
        ass_path = output_directory / "captions.ass"
        srt_path = output_directory / "captions.srt"
        transcript_path = output_directory / "transcript.json"
        project_path = output_directory / "project.acs"
        video_path = output_directory / _safe_output_name(output_name)
        ass_path.write_text(
            self.editor_ass_text(project, metadata), encoding="utf-8"
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
                    crf=crf,
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

    def editor_ass_text(
        self, project: CaptionEditorProject, metadata: VideoMetadata
    ) -> str:
        captions = tuple(caption.to_caption() for caption in project.captions)
        layouts = tuple(
            (
                caption.x,
                caption.y,
                caption.scale if caption.scale_override else 1.0,
                caption.word_spacing,
                caption.rotation_deg,
            )
            for caption in project.captions
        )
        return self._ass.generate(
            captions,
            project.style_id,
            project.style_config,
            metadata.width,
            metadata.height,
            caption_layouts=layouts,
            caption_font_sizes=tuple(
                min(
                    caption.layout_font_size or project.style_config.font_size,
                    project.style_config.font_size,
                )
                for caption in project.captions
            ),
            caption_style_ids=tuple(
                caption.style_id for caption in project.captions
            ),
            caption_widths=tuple(
                caption.max_width for caption in project.captions
            ),
        )

    def render_editor_preview_frame(
        self,
        project: CaptionEditorProject,
        metadata: VideoMetadata,
        seconds: float,
    ) -> bytes:
        with tempfile.TemporaryDirectory(prefix="caption-preview-") as directory:
            ass_path = Path(directory) / "preview.ass"
            ass_path.write_text(self.editor_ass_text(project, metadata), encoding="utf-8")
            return self._ffmpeg.preview_frame(project.source_video, ass_path, seconds)


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^\w.-]+", "_", name, flags=re.UNICODE).strip("._")
    return cleaned[:80] or "proyecto"


def _safe_output_name(name: str) -> str:
    candidate = Path(name).name
    if not candidate.lower().endswith(".mp4"):
        candidate += ".mp4"
    cleaned = re.sub(r"[^\w.-]+", "_", candidate, flags=re.UNICODE).strip("._")
    if not cleaned or cleaned.lower() == ".mp4":
        raise ValueError("El nombre del archivo de salida no es válido.")
    return cleaned[:120]


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


def _write_json_atomic(path: Path, data: dict) -> None:
    import os

    temporary_path: Path | None = None
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(data, temporary_file, ensure_ascii=False, indent=2)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
