import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.core.models import (
    Caption,
    CaptionStyleConfig,
    TranscriptSegment,
    TranscriptionResult,
    Word,
)
from app.editor.models import CaptionEditorProject
from app.editor.service import CaptionEditorService
from app.ui.timeline_editor import TimelineEditor


class TimelineEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        words = (Word("Hola", 0.5, 1.5),)
        transcript = TranscriptionResult(
            "es", (TranscriptSegment(0.5, 1.5, words),)
        )
        project = CaptionEditorProject.create(
            "source.mp4",
            transcript,
            (Caption(0.5, 1.5, words),),
            "tiktok_pop",
            CaptionStyleConfig(),
        )
        self.editor = CaptionEditorService(project)
        self.timeline = TimelineEditor(self.editor, 2)
        self.timeline.resize(400, 130)
        self.timeline.show()
        self.application.processEvents()

    def tearDown(self) -> None:
        self.timeline.close()

    def test_clicking_caption_selects_it_and_empty_area_seeks(self) -> None:
        selected: list[str] = []
        sought: list[float] = []
        self.timeline.captionSelected.connect(selected.append)
        self.timeline.seekRequested.connect(sought.append)
        QTest.mouseClick(self.timeline, Qt.MouseButton.LeftButton, pos=QPoint(150, 60))
        QTest.mouseClick(self.timeline, Qt.MouseButton.LeftButton, pos=QPoint(350, 60))
        self.assertEqual(selected, [self.editor.project.captions[0].caption_id])
        self.assertAlmostEqual(sought[0], 1.75)

    def test_dragging_caption_edge_updates_timing(self) -> None:
        changed: list[tuple[str, float, float]] = []
        self.timeline.timingChanged.connect(
            lambda caption_id, start, end: changed.append((caption_id, start, end))
        )
        QTest.mousePress(
            self.timeline, Qt.MouseButton.LeftButton, pos=QPoint(100, 60)
        )
        QTest.mouseMove(self.timeline, QPoint(140, 60), 20)
        QTest.mouseRelease(
            self.timeline, Qt.MouseButton.LeftButton, pos=QPoint(140, 60)
        )
        self.assertTrue(changed)
        self.assertEqual(changed[-1][0], self.editor.project.captions[0].caption_id)
        self.assertAlmostEqual(changed[-1][1], 0.7, places=2)
        self.assertAlmostEqual(changed[-1][2], 1.5, places=2)

    def test_zoomed_timeline_can_scroll_and_recenter_on_playhead(self) -> None:
        view_changes: list[tuple[float, float]] = []
        self.timeline.viewChanged.connect(
            lambda start, duration: view_changes.append((start, duration))
        )
        self.timeline.zoom(2)
        self.assertAlmostEqual(self.timeline._view_duration, 1)
        self.timeline.set_view_start(0.75)
        self.assertAlmostEqual(self.timeline._view_start, 0.75)
        self.timeline.set_current_time(0.1)
        self.assertAlmostEqual(self.timeline._view_start, 0)
        self.assertTrue(view_changes)


if __name__ == "__main__":
    unittest.main()
