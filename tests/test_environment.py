import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.rendering.ffmpeg import FFmpegService
from app.services.environment import (
    DependencyStatus,
    EnvironmentService,
    active_python_executable,
)


class EnvironmentTests(unittest.TestCase):
    def test_diagnosis_reports_installed_runtime_dependencies(self) -> None:
        service = EnvironmentService(FFmpegService())
        with patch.object(service, "_check_faster_whisper") as whisper, patch.object(
            service, "_check_ffmpeg"
        ) as ffmpeg, patch.object(service, "_check_ffprobe") as ffprobe:
            whisper.return_value = DependencyStatus("faster-whisper", True, "1.2.1", "")
            ffmpeg.return_value = DependencyStatus("FFmpeg", True, "9.0.2", "")
            ffprobe.return_value = DependencyStatus("ffprobe", True, "9.0.2", "")
            report = service.diagnose()

        self.assertTrue(report.ready)
        self.assertTrue(report.python_executable.endswith("/python"))
        self.assertEqual(report.python_version.count("."), 2)

    def test_ffmpeg_missing_libass_blocks_generation(self) -> None:
        ffmpeg = FFmpegService(executable="/missing/ffmpeg", probe="/missing/ffprobe")
        service = EnvironmentService(ffmpeg)
        with patch("app.services.environment.subprocess.run", side_effect=FileNotFoundError):
            report = service.diagnose()
        self.assertFalse(report.ready)
        self.assertFalse(report.ffmpeg.available)
        self.assertFalse(report.ffprobe.available)

    def test_ffmpeg_validation_failure_has_actionable_diagnostic(self) -> None:
        ffmpeg = FFmpegService(executable="/missing/ffmpeg", probe=None)
        service = EnvironmentService(ffmpeg)
        no_ass_filter = SimpleNamespace(returncode=0, stdout="")
        valid_binary = SimpleNamespace(returncode=0, stdout="ffmpeg version 9.0.2")
        with patch(
            "app.services.environment.subprocess.run",
            side_effect=[valid_binary, no_ass_filter],
        ):
            status = service._check_ffmpeg()
        self.assertFalse(status.available)
        self.assertIn("libass", status.details)
        self.assertIn("ffmpeg-full", status.remedy)

    def test_reports_missing_dependency_from_active_interpreter(self) -> None:
        service = EnvironmentService(FFmpegService())
        with (
            patch("app.services.environment.importlib.util.find_spec", return_value=None),
            patch("app.services.environment.subprocess.run") as run,
            patch(
                "app.services.environment.active_python_executable",
                return_value="/test/venv/bin/python",
            ),
        ):
            run.return_value.returncode = 1
            status = service._check_faster_whisper()
        self.assertFalse(status.available)
        self.assertIn("/test/venv/bin/python", status.remedy)

    def test_uses_venv_python_when_macos_resolves_executable_to_base_python(self) -> None:
        with (
            patch("app.services.environment.sys.prefix", "/test/venv"),
            patch("app.services.environment.sys.base_prefix", "/opt/homebrew/python"),
            patch("app.services.environment.Path.is_file", return_value=True),
        ):
            self.assertEqual(active_python_executable(), "/test/venv/bin/python")


if __name__ == "__main__":
    unittest.main()
