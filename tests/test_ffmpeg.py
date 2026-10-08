import unittest
from pathlib import Path
from unittest.mock import patch

from app.rendering.ffmpeg import _escape_filter_path, _locate_executable


class FFmpegTests(unittest.TestCase):
    def test_ffmpeg_filter_path_escapes_special_characters(self) -> None:
        escaped = _escape_filter_path(Path("/tmp/A folder/a:b,c.ass"))
        self.assertIn(r"A folder/a\:b\,c.ass", escaped)

    def test_prefers_homebrew_full_build_when_available(self) -> None:
        def is_file(path: Path) -> bool:
            return "ffmpeg-full" in str(path)

        with (
            patch("app.rendering.ffmpeg.shutil.which", return_value="/opt/homebrew/bin/ffmpeg"),
            patch.object(Path, "is_file", is_file),
        ):
            self.assertEqual(
                _locate_executable("ffmpeg"),
                "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",
            )
