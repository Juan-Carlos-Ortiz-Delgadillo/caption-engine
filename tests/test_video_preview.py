import unittest

from app.core.models import Caption, VideoMetadata, Word
from app.ui.preview_geometry import (
    Rect,
    caption_handle_at,
    hit_test_caption,
    moved_position,
    resized_width,
    scaled_value,
    video_rect,
    video_to_widget,
    widget_to_video,
)
from app.ui.video_preview import (
    _active_caption,
    _fit_video_rect,
    _format_time,
    _preview_video_rect,
)


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

    def test_preview_ratio_and_zoom_map_canvas_coordinates_to_source(self) -> None:
        rect = _preview_video_rect(
            900, 600, VideoMetadata(1920, 1080, 10), 9 / 16, 2
        )
        self.assertLess(rect.left(), 0)
        self.assertAlmostEqual(rect.center().x(), 450)
        self.assertEqual(rect.width(), 2 * _preview_video_rect(
            900, 600, VideoMetadata(1920, 1080, 10), 9 / 16, 1
        ).width())

    def test_time_label_formats_hours_when_needed(self) -> None:
        self.assertEqual(_format_time(65_000), "01:05")
        self.assertEqual(_format_time(3_661_000), "1:01:01")

    def test_coordinate_mapping_round_trips_aspect_ratios_zoom_and_pan(self) -> None:
        cases = (
            (1920, 1080, 1000, 700, None, 1.0),
            (1080, 1920, 900, 600, None, 1.8),
            (1080, 1080, 500, 800, 1.0, 1.4),
            (1920, 1080, 1200, 700, 9 / 16, 1.0),
        )
        for width, height, widget_w, widget_h, ratio, zoom in cases:
            with self.subTest(width=width, height=height, ratio=ratio, zoom=zoom):
                rect = video_rect(
                    widget_w,
                    widget_h,
                    width,
                    height,
                    zoom=zoom,
                    target_ratio=ratio,
                    pan_x=11,
                    pan_y=-7,
                )
                for point in ((width * 0.2, height * 0.25), (width * 0.5, height * 0.5)):
                    widget_point = video_to_widget(*point, rect, width, height)
                    mapped = widget_to_video(*widget_point, rect, width, height)
                    self.assertIsNotNone(mapped)
                    self.assertAlmostEqual(mapped[0], point[0])
                    self.assertAlmostEqual(mapped[1], point[1])

    def test_caption_hit_testing_and_handles(self) -> None:
        bounds = Rect(100, 80, 200, 60)
        self.assertTrue(hit_test_caption(150, 100, bounds))
        self.assertFalse(hit_test_caption(90, 70, bounds))
        self.assertEqual(caption_handle_at(300, 80, bounds), "resize")
        self.assertEqual(caption_handle_at(200, 80, bounds), "width")
        self.assertEqual(caption_handle_at(300, 158, bounds), "rotate")
        self.assertIsNone(caption_handle_at(150, 110, bounds))

    def test_drag_and_resize_math_uses_video_coordinates(self) -> None:
        self.assertEqual(
            moved_position(0.45, 0.3, 100, 30, 1000, 500, lock_axis=True),
            (0.55, 0.3),
        )
        self.assertEqual(
            moved_position(0.49, 0.49, 5, 5, 1000, 500), (0.5, 0.5)
        )
        self.assertEqual(scaled_value(1.0, 100, 150), 1.5)
        self.assertEqual(scaled_value(3.0, 100, 200), 4.0)
        self.assertEqual(resized_width(0.8, 50, 1000), 0.9)


if __name__ == "__main__":
    unittest.main()
