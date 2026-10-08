import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

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
from app.editor.service import CaptionEditorService
from app.rendering.ffmpeg import FFmpegService
from app.services.pipeline import CaptionApplicationService
from app.srt.generator import SRTGenerator
from app.transcription.base import TranscriptionEngine
from app.ui.caption_editor_window import CaptionEditorWindow


class UnusedTranscriptionEngine(TranscriptionEngine):
    def transcribe(self, media_path, options, progress=None):
        raise AssertionError("Opening the editor must not transcribe again.")


class CaptionEditorWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_window_edits_captions_and_saves_the_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.touch()
            words = (
                Word("Hola", 0.0, 0.3),
                Word("todos", 0.3, 0.6),
                Word("aquí", 0.6, 0.9),
            )
            transcript = TranscriptionResult(
                "es", (TranscriptSegment(0, 0.9, words),)
            )
            project = CaptionEditorProject.create(
                source,
                transcript,
                (Caption(0, 0.9, words),),
                "tiktok_pop",
                CaptionStyleConfig(),
                TranscriptionOptions(),
            )
            editor = CaptionEditorService(project)
            export_service = CaptionApplicationService(
                UnusedTranscriptionEngine(),
                CaptionSegmenter(),
                ASSGenerator(),
                SRTGenerator(),
                FFmpegService(executable="ffmpeg", probe="ffprobe"),
            )
            project_path = root / "project.acs"
            window = CaptionEditorWindow(
                editor,
                VideoMetadata(1920, 1080, 1),
                project_path,
                export_service,
            )
            window.show()
            self.application.processEvents()

            self.assertEqual(window._tabs.count(), 5)
            window._text.setPlainText("Hola gente aquí")
            self.application.processEvents()
            self.assertEqual(editor.project.captions[0].text, "Hola gente aquí")
            window._text.setFocus()
            cursor = window._text.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            window._text.setTextCursor(cursor)
            QTest.keyClick(window._text, Qt.Key.Key_S)
            self.application.processEvents()
            self.assertEqual(len(editor.project.captions), 1)
            self.assertEqual(editor.project.captions[0].text, "Hola gente aquís")
            window._text.setPlainText("Hola gente aquí")
            window._split_caption()
            self.assertEqual(len(editor.project.captions), 2)
            window._undo()
            self.assertEqual(len(editor.project.captions), 1)
            window._save_project()
            self.assertEqual(json.loads(project_path.read_text())["version"], 2)
            window.close()
            self.application.processEvents()


if __name__ == "__main__":
    unittest.main()
