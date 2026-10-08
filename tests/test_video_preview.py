import unittest

from app.core.models import Caption, VideoMetadata, Word
from app.ui.video_preview import _active_caption, _fit_video_rect, _format_time


class VideoPreviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.captions = (
            Caption(0, 0.5, (Word("Hola", 0, 0.5),)),
            Caption(0.5, 1.0, (Word("todos", 0.5, 1.0),)),
        )

    def test_active_caption_tracks_playback_time(self) -> None:
        self.assertIs(self.captions[0], _active_caption(self.captions, 0.25))
        self.assertIs(self.captions[1], _active_caption(self.captions, 0.5))
        self.assertIsNone(_active_caption(self.captions, 1.0))

    def test_video_rect_preserves_source_aspect_ratio(self) -> None:
        rect = _fit_video_rect(800, 600, VideoMetadata(1920, 1080, 10))
        self.assertEqual(rect.width(), 800)
        self.assertEqual(rect.height(), 450)
        self.assertEqual(rect.y(), 75)

    def test_zero_sized_preview_has_empty_video_rect(self) -> None:
        rect = _fit_video_rect(0, 600, VideoMetadata(1920, 1080, 10))
        self.assertTrue(rect.isEmpty())

    def test_time_label_formats_hours_when_needed(self) -> None:
        self.assertEqual(_format_time(65_000), "01:05")
        self.assertEqual(_format_time(3_661_000), "1:01:01")


if __name__ == "__main__":
    unittest.main()
