import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.ass.generator import ASSGenerator
from app.captions.segmenter import CaptionSegmenter
from app.core.models import (
    Caption,
    CaptionStyleConfig,
    TranscriptSegment,
    TranscriptionOptions,
    TranscriptionResult,
    VideoMetadata,
    Word,
)
from app.editor.models import CaptionEditorProject
from app.rendering.ffmpeg import FFmpegService
from app.services.pipeline import CaptionApplicationService
from app.srt.generator import SRTGenerator
from app.transcription.base import TranscriptionEngine


class UnusedTranscriptionEngine(TranscriptionEngine):
    def transcribe(self, media_path, options, progress=None):
        raise AssertionError("The editor export must not transcribe the source again.")


class EditorExportTests(unittest.TestCase):
    def test_exports_edited_text_and_normalized_position(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.touch()
            original_words = (Word("Original", 0.0, 0.4), Word("text", 0.4, 0.8))
            transcript = TranscriptionResult(
                "en", (TranscriptSegment(0, 0.8, original_words),)
            )
            project = CaptionEditorProject.create(
                source,
                transcript,
                (Caption(0, 0.8, original_words),),
                "tiktok_pop",
                CaptionStyleConfig(),
            )
            caption = project.captions[0]
            from dataclasses import replace

            project = replace(
                project,
                captions=(
                    replace(
                        caption,
                        words=(Word("Edited", 0.0, 0.4), Word("caption", 0.4, 0.8)),
                        x=0.25,
                        y=0.75,
                        scale=1.5,
                        word_spacing=4,
                    ),
                ),
                edited=True,
            )
            service = CaptionApplicationService(
                UnusedTranscriptionEngine(),
                CaptionSegmenter(),
                ASSGenerator(),
                SRTGenerator(),
                FFmpegService(executable="ffmpeg", probe="ffprobe"),
            )
            with patch.object(FFmpegService, "check") as check, patch.object(
                FFmpegService, "render"
            ) as render:
                result = service.export_editor_project(
                    project,
                    VideoMetadata(1920, 1080, 1),
                    root / "output",
                    render_video=False,
                )
            self.assertIn("Edited caption", result.srt.read_text(encoding="utf-8"))
            ass_text = result.ass.read_text(encoding="utf-8")
            self.assertIn(r"\pos(480,810)", ass_text)
            self.assertIn(r"\fscx150\fscy150\fsp4", ass_text)
            check.assert_not_called()
            render.assert_not_called()
            self.assertEqual(project.original_transcript.words[0].text, "Original")


if __name__ == "__main__":
    unittest.main()
