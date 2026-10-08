import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from app.rendering.ffmpeg import FFmpegService, _escape_filter_path, _locate_executable


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

    def test_ignores_unavailable_progress_timestamp(self) -> None:
        process = Mock(
            stdout=["out_time_us=N/A\n", "progress=continue\n", "out_time_us=500000\n"],
            wait=Mock(return_value=0),
        )
        progress = Mock()
        service = FFmpegService(executable="ffmpeg", probe="ffprobe")

        with patch("app.rendering.ffmpeg.subprocess.Popen", return_value=process):
            service.run(["ffmpeg"], duration=1, progress=progress)

        self.assertEqual(progress.call_args_list, [call(0.5), call(1.0)])
