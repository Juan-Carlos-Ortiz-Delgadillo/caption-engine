import json
import os
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.core.models import (
    Caption,
    CaptionStyleConfig,
    TranscriptionOptions,
    TranscriptionResult,
    TranscriptSegment,
    VideoMetadata,
    Word,
)
from app.editor.models import CaptionEditorProject, project_from_dict
from app.editor.service import CaptionEditorService


def make_project() -> CaptionEditorProject:
    words = (
        Word("Hola", 0.0, 0.4, 0.9),
        Word("todos", 0.4, 0.8, 0.8),
        Word("amigos", 0.8, 1.2, 0.7),
    )
    transcript = TranscriptionResult(
        "es", (TranscriptSegment(0.0, 1.2, words),)
    )
    return CaptionEditorProject.create(
        Path("/tmp/video.mp4"),
        transcript,
        (
            Caption(0.0, 0.8, words[:2]),
            Caption(0.8, 1.2, words[2:]),
        ),
        "tiktok_pop",
        CaptionStyleConfig(),
    )


class CaptionEditorServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.application = QApplication.instance() or QApplication([])
        self.project = make_project()
        self.service = CaptionEditorService(self.project)
        self.first, self.second = self.project.captions

    def test_text_update_keeps_timestamps_and_original_transcript(self) -> None:
        self.service.update_text(self.first.caption_id, "Buenas\ngente")
        updated = self.service.project.captions[0]
        self.assertEqual(updated.text, "Buenas\ngente")
        self.assertEqual(updated.line_breaks, (0,))
        self.assertEqual(
            [(word.start, word.end) for word in updated.words],
            [(0.0, 0.4), (0.4, 0.8)],
        )
        self.assertEqual(self.service.project.original_transcript.words[0].text, "Hola")

    def test_split_merge_and_undo_redo_preserve_word_data(self) -> None:
        left_id, right_id = self.service.split_caption(self.first.caption_id, 1)
        self.assertEqual([caption.text for caption in self.service.project.captions[:2]], ["Hola", "todos"])
        self.assertNotEqual(left_id, right_id)
        self.service.merge_with_next(left_id)
        self.assertEqual(self.service.project.captions[0].text, "Hola todos")
        self.assertTrue(self.service.undo())
        self.assertEqual([caption.text for caption in self.service.project.captions[:2]], ["Hola", "todos"])
        self.assertTrue(self.service.redo())
        self.assertEqual(self.service.project.captions[0].text, "Hola todos")
        self.assertEqual(self.service.project.original_transcript.words[0].text, "Hola")

    def test_reorder_and_move_words_retain_word_timestamps(self) -> None:
        self.service.reorder_word(self.first.caption_id, 0, 1)
        self.assertEqual(self.service.project.captions[0].text, "todos Hola")
        self.assertEqual(self.service.project.captions[0].words[1], self.first.words[0])
        self.service.move_word(self.first.caption_id, 0, self.second.caption_id, 0)
        self.assertEqual([caption.text for caption in self.service.project.captions], ["Hola", "todos amigos"])
        moved = self.service.project.captions[1].words[0]
        self.assertEqual((moved.start, moved.end, moved.confidence), (0.4, 0.8, 0.8))

    def test_position_timing_and_spacing_are_validated_and_undoable(self) -> None:
        self.service.move_caption(self.first.caption_id, 0.2, 0.7)
        self.service.set_word_spacing(self.first.caption_id, 2.5)
        self.service.set_timing(self.first.caption_id, 0.1, 0.9)
        updated = self.service.project.captions[0]
        self.assertEqual((updated.x, updated.y, updated.word_spacing), (0.2, 0.7, 2.5))
        self.assertEqual((updated.start, updated.end), (0.1, 0.9))
        with self.assertRaises(ValueError):
            self.service.set_timing(self.first.caption_id, 1, 1)
        with self.assertRaises(ValueError):
            self.service.move_caption(self.first.caption_id, float("nan"), 0.5)
        self.assertTrue(self.service.undo())
        self.assertEqual(self.service.project.captions[0].end, 0.8)

    def test_canvas_drag_merges_into_a_single_undo_step(self) -> None:
        self.service.move_caption(
            self.first.caption_id, 0.4, 0.7, merge_token="drag-1"
        )
        self.service.move_caption(
            self.first.caption_id, 0.3, 0.6, merge_token="drag-1"
        )
        self.assertEqual(
            (self.service.project.captions[0].x, self.service.project.captions[0].y),
            (0.3, 0.6),
        )
        self.assertTrue(self.service.undo())
        self.assertEqual(
            (self.service.project.captions[0].x, self.service.project.captions[0].y),
            (0.5, 0.82),
        )

    def test_reset_geometry_uses_current_global_position(self) -> None:
        self.service.move_caption(self.first.caption_id, 0.2, 0.3)
        self.service.resize_caption(self.first.caption_id, 1.75)
        self.service.reset_caption_geometry(self.first.caption_id)
        caption = self.service.project.captions[0]
        self.assertEqual((caption.x, caption.y, caption.scale), (0.5, 0.82, 1.0))
        self.assertFalse(caption.position_override)
        self.assertFalse(caption.scale_override)

    def test_project_round_trip_and_save_keep_original_data(self) -> None:
        self.service.update_text(self.first.caption_id, "Hola\na todos")
        self.service.rotate_caption(self.first.caption_id, 12.5)
        restored = project_from_dict(self.service.project.to_dict())
        self.assertEqual(restored, self.service.project)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project.acs"
            self.service.save(path)
            loaded = CaptionEditorService.load(path)
        self.assertEqual(loaded.project, self.service.project)
        self.assertEqual(loaded.project.original_transcript.words[0].text, "Hola")

    def test_loads_legacy_generated_project_without_retranscribing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transcript_path = root / "transcript.json"
            transcript_path.write_text(
                json.dumps(
                    {
                        "language": "es",
                        "language_probability": None,
                        "segments": [
                            {
                                "start": segment.start,
                                "end": segment.end,
                                "words": [asdict(word) for word in segment.words],
                            }
                            for segment in self.project.original_transcript.segments
                        ],
                    }
                ),
                encoding="utf-8",
            )
            project_path = root / "project.acs"
            project_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "source_video": str(self.project.source_video),
                        "transcription": asdict(TranscriptionOptions()),
                        "style_id": self.project.style_id,
                        "style_config": asdict(self.project.style_config),
                    }
                ),
                encoding="utf-8",
            )
            loaded = CaptionEditorService.load(project_path)
        self.assertEqual(
            loaded.project.original_transcript.words,
            self.project.original_transcript.words,
        )
        self.assertEqual(loaded.project.captions[0].text, "Hola todos amigos")
        self.assertIsNone(loaded.project.video_metadata)

    def test_migrates_version_two_projects_when_saved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project.acs"
            data = self.project.to_dict()
            data["version"] = 2
            for caption in data["captions"]:
                caption.pop("line_breaks_manual")
                caption.pop("layout_font_size")
                caption.pop("layout_warning")
            path.write_text(json.dumps(data), encoding="utf-8")

            loaded = CaptionEditorService.load(path)
            loaded.save(path)

            saved_data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved_data["version"], 4)
        self.assertFalse(loaded.project.captions[0].line_breaks_manual)

    def test_timing_edits_keep_captions_in_chronological_order(self) -> None:
        self.service.set_timing(self.first.caption_id, 1.3, 1.6)
        self.assertEqual(
            [caption.caption_id for caption in self.service.project.captions],
            [self.second.caption_id, self.first.caption_id],
        )

    def test_relayout_preserves_caption_edits_and_is_one_undoable_change(self) -> None:
        self.service = CaptionEditorService(
            replace(self.project, video_metadata=VideoMetadata(1920, 1080, 1.2))
        )
        self.service.move_caption(self.first.caption_id, 0.23, 0.71)
        self.service.update_text(self.first.caption_id, "Buenas\ngente")
        self.service.set_timing(self.first.caption_id, 0.1, 0.9)
        self.service.set_style(
            "minimal", replace(self.project.style_config, font_size=48)
        )
        before_relayout = self.service.project

        self.service.relayout()

        updated = self.service.project.captions[0]
        self.assertEqual(updated.text, "Buenas\ngente")
        self.assertEqual((updated.start, updated.end), (0.1, 0.9))
        self.assertEqual((updated.x, updated.y), (0.23, 0.71))
        self.assertTrue(updated.line_breaks_manual)
        self.assertTrue(self.service.undo())
        self.assertEqual(self.service.project, before_relayout)

    def test_unfittable_caption_keeps_content_and_exposes_undoable_warning(self) -> None:
        project = replace(
            self.project,
            video_metadata=VideoMetadata(100, 100, 1),
        )
        self.service = CaptionEditorService(project)

        self.service.relayout()

        caption = self.service.project.captions[0]
        self.assertEqual(caption.text, self.first.text)
        self.assertIsNotNone(caption.layout_warning)
        self.assertTrue(self.service.undo())
        self.assertIsNone(self.service.project.captions[0].layout_warning)

    def test_relayout_never_hyphenates_or_replaces_an_existing_caption(self) -> None:
        word = Word("x" * 160, 0.0, 1.0)
        transcript = TranscriptionResult(
            "es", (TranscriptSegment(0.0, 1.0, (word,)),)
        )
        project = CaptionEditorProject.create(
            Path("/tmp/video.mp4"),
            transcript,
            (Caption(0.0, 1.0, (word,)),),
            "tiktok_pop",
            CaptionStyleConfig(),
            video_metadata=VideoMetadata(320, 480, 1.0),
        )
        self.service = CaptionEditorService(project)

        self.service.relayout()

        caption = self.service.project.captions[0]
        self.assertEqual(caption.text, word.text)
        self.assertEqual(caption.words, (word,))
        self.assertIsNotNone(caption.layout_warning)

    def test_relayout_preserves_caption_order_and_style_overrides(self) -> None:
        reversed_captions = tuple(
            replace(caption, style_id="one_word")
            for caption in reversed(self.project.captions)
        )
        project = replace(
            self.project,
            captions=reversed_captions,
            video_metadata=VideoMetadata(1920, 1080, 1.2),
        )
        self.service = CaptionEditorService(project)
        original_ids = tuple(caption.caption_id for caption in reversed_captions)

        self.service.relayout()

        self.assertEqual(
            tuple(caption.caption_id for caption in self.service.project.captions),
            original_ids,
        )
        self.assertTrue(
            all(caption.style_id == "one_word" for caption in self.service.project.captions)
        )

    def test_rebuild_from_transcript_is_one_undoable_operation(self) -> None:
        self.service = CaptionEditorService(
            replace(self.project, video_metadata=VideoMetadata(1920, 1080, 1.2))
        )
        self.service.update_text(self.first.caption_id, "Edición manual")
        before_rebuild = self.service.project

        self.service.rebuild_from_transcript()

        self.assertEqual(
            " ".join(caption.text for caption in self.service.project.captions),
            "Hola todos amigos",
        )
        self.assertTrue(self.service.undo())
        self.assertEqual(self.service.project, before_rebuild)


if __name__ == "__main__":
    unittest.main()
