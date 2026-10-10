import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.ass.generator import ASSGenerator
from app.captions.segmenter import CaptionSegmenter
from app.core.models import (
    CaptionStyleConfig,
    TranscriptionOptions,
    TranscriptionResult,
    TranscriptSegment,
    VideoMetadata,
    Word,
)
from app.rendering.ffmpeg import FFmpegService
from app.services.pipeline import CaptionApplicationService
from app.srt.generator import SRTGenerator
from app.transcription.base import ProgressCallback, TranscriptionEngine


class FakeTranscriptionEngine(TranscriptionEngine):
    def transcribe(
        self,
        media_path: Path,
        options: TranscriptionOptions,
        progress: ProgressCallback | None = None,
    ) -> TranscriptionResult:
        words = (Word("Hola.", 0, 0.5),)
        return TranscriptionResult("es", (TranscriptSegment(0, 0.5, words),))


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_writes_transcript_ass_srt_and_rendered_video(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "video.mp4"
            source.touch()
            service = CaptionApplicationService(
                FakeTranscriptionEngine(),
                CaptionSegmenter(),
                ASSGenerator(),
                SRTGenerator(),
                FFmpegService(executable="ffmpeg", probe="ffprobe"),
            )
            with (
                patch.object(FFmpegService, "check"),
                patch.object(
                    FFmpegService, "metadata", return_value=VideoMetadata(1920, 1080, 3)
                ),
                patch.object(FFmpegService, "render") as render,
            ):
                result = service.generate(
                    source,
                    root / "output",
                    TranscriptionOptions(),
                    "tiktok_pop",
                    CaptionStyleConfig(),
                )

            self.assertTrue(result.ass.is_file())
            self.assertTrue(result.srt.is_file())
            self.assertTrue(result.transcript.is_file())
            self.assertTrue(result.project.is_file())
            self.assertEqual(result.source_video, source)
            self.assertEqual(result.captions[0].text, "Hola.")
            self.assertEqual(result.metadata, VideoMetadata(1920, 1080, 3))
            transcript = json.loads(result.transcript.read_text())
            self.assertEqual(transcript["duration"], 0.5)
            self.assertEqual(len(transcript["words"]), 1)
            self.assertEqual(transcript["segments"][0]["words"][0]["text"], "Hola.")
            project = json.loads(result.project.read_text())
            self.assertEqual(project["style_id"], "tiktok_pop")
            render.assert_called_once()

    def test_never_overwrites_a_previous_project_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "video.mp4"
            source.touch()
            output_root = root / "output"
            previous = output_root / "video"
            previous.mkdir(parents=True)
            (previous / "important.txt").write_text("preservar")
            service = CaptionApplicationService(
                FakeTranscriptionEngine(),
                CaptionSegmenter(),
                ASSGenerator(),
                SRTGenerator(),
                FFmpegService(executable="ffmpeg", probe="ffprobe"),
            )
            with (
                patch.object(FFmpegService, "check"),
                patch.object(
                    FFmpegService, "metadata", return_value=VideoMetadata(1920, 1080, 3)
                ),
                patch.object(FFmpegService, "render"),
            ):
                result = service.generate(
                    source,
                    output_root,
                    TranscriptionOptions(),
                    "tiktok_pop",
                    CaptionStyleConfig(),
                )
            self.assertEqual(result.output_directory.name, "video_2")
            self.assertEqual((previous / "important.txt").read_text(), "preservar")

    def test_transcript_can_be_reused_without_calling_whisper_again(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "video.mp4"
            source.touch()
            engine = FakeTranscriptionEngine()
            service = CaptionApplicationService(
                engine,
                CaptionSegmenter(),
                ASSGenerator(),
                SRTGenerator(),
                FFmpegService(executable="ffmpeg", probe="ffprobe"),
            )
            options = TranscriptionOptions()
            metadata = VideoMetadata(1920, 1080, 3)
            with (
                patch.object(FFmpegService, "metadata", return_value=metadata),
                patch.object(engine, "transcribe", wraps=engine.transcribe) as transcribe,
            ):
                original, _, transcript_path = service.transcribe_to_workspace(
                    source, root / "first", options
                )
                reused, reused_metadata, reused_path = service.transcribe_to_workspace(
                    source, root / "second", options, transcript_path
                )

            transcribe.assert_called_once()
            self.assertEqual(original, reused)
            self.assertEqual(reused_metadata, metadata)
            self.assertTrue(reused_path.is_file())
            self.assertNotEqual(transcript_path, reused_path)

    def test_layout_stage_does_not_export_or_render(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "video.mp4"
            source.touch()
            transcript = FakeTranscriptionEngine().transcribe(
                source, TranscriptionOptions()
            )
            service = CaptionApplicationService(
                FakeTranscriptionEngine(),
                CaptionSegmenter(),
                ASSGenerator(),
                SRTGenerator(),
                FFmpegService(executable="ffmpeg", probe="ffprobe"),
            )
            metadata = VideoMetadata(1920, 1080, 3)
            with (
                patch.object(FFmpegService, "check") as check,
                patch.object(FFmpegService, "render") as render,
                patch.object(service._ass, "generate") as generate_ass,
                patch.object(service._srt, "generate") as generate_srt,
            ):
                project = service.build_editor_project(
                    source,
                    transcript,
                    TranscriptionOptions(),
                    "tiktok_pop",
                    CaptionStyleConfig(),
                    metadata,
                )

            self.assertEqual(project.captions[0].text, "Hola.")
            self.assertEqual(project.video_metadata, metadata)
            check.assert_not_called()
            render.assert_not_called()
            generate_ass.assert_not_called()
            generate_srt.assert_not_called()

    def test_finds_transcript_in_recent_project_for_same_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "video.mp4"
            source.touch()
            workspace = root / "workspace"
            workspace.mkdir()
            project_path = workspace / "project.acs"
            transcript_path = workspace / "transcript.json"
            project_path.write_text(json.dumps({"source_video": str(source)}))
            transcript_path.write_text("{}")
            service = CaptionApplicationService(
                FakeTranscriptionEngine(),
                CaptionSegmenter(),
                ASSGenerator(),
                SRTGenerator(),
                FFmpegService(executable="ffmpeg", probe="ffprobe"),
            )

            found = service.reusable_transcripts(
                source, root / "output", (project_path,)
            )

        self.assertEqual(found, (transcript_path,))
