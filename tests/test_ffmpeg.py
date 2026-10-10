import tempfile
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

    def test_render_replaces_output_only_after_temporary_render_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            subtitles = root / "captions.ass"
            output = root / "result.mp4"
            source.touch()
            subtitles.touch()
            output.write_bytes(b"previous")
            service = FFmpegService(executable="ffmpeg", probe="ffprobe")

            def render_to_temporary(command, _duration, _progress):
                Path(command[-1]).write_bytes(b"new render")

            with patch.object(service, "run", side_effect=render_to_temporary) as run:
                service.render(source, subtitles, output, 1)

            self.assertEqual(output.read_bytes(), b"new render")
            self.assertNotEqual(run.call_args.args[0][-1], str(output))
            self.assertEqual(list(root.glob(".result.*.mp4")), [])

    def test_failed_atomic_render_preserves_previous_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            subtitles = root / "captions.ass"
            output = root / "result.mp4"
            source.touch()
            subtitles.touch()
            output.write_bytes(b"previous")
            service = FFmpegService(executable="ffmpeg", probe="ffprobe")
            with (
                patch.object(service, "run", side_effect=RuntimeError("failed")),
                self.assertRaisesRegex(RuntimeError, "failed"),
            ):
                service.render(source, subtitles, output, 1)
            self.assertEqual(output.read_bytes(), b"previous")
            self.assertEqual(list(root.glob(".result.*.mp4")), [])
