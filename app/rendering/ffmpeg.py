"""Ejecución segura de FFmpeg/FFprobe y parseo de progreso."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

from app.core.exceptions import DependencyError, RenderingError
from app.core.models import VideoMetadata

logger = logging.getLogger(__name__)
ProgressCallback = Callable[[float], None]


class FFmpegService:
    def __init__(self, executable: str | None = None, probe: str | None = None) -> None:
        self.executable = executable or _locate_executable("ffmpeg")
        self.probe = probe or _locate_executable("ffprobe")

    def check(self) -> str:
        if not self.executable:
            raise DependencyError(
                "No se encontró FFmpeg. Instálalo o configura su ubicación."
            )
        result = subprocess.run(
            [self.executable, "-version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if result.returncode:
            raise DependencyError("No se pudo iniciar FFmpeg.")
        filters = subprocess.run(
            [self.executable, "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if filters.returncode or not any(
            len(columns := line.split()) > 1 and columns[1] == "ass"
            for line in filters.stdout.splitlines()
        ):
            raise DependencyError(
                "FFmpeg está instalado, pero no incluye el filtro ASS/libass. "
                "Instala una compilación de FFmpeg con soporte libass."
            )
        return result.stdout.splitlines()[0] if result.stdout else "FFmpeg disponible"

    def metadata(self, media: Path) -> VideoMetadata:
        if not self.probe:
            raise DependencyError("No se encontró FFprobe, incluido con FFmpeg.")
        result = subprocess.run(
            [
                self.probe,
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=width,height,codec_type",
                "-of",
                "json",
                str(media),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if result.returncode:
            raise RenderingError("No se pudieron leer los datos del video.")
        try:
            data = json.loads(result.stdout)
            video = next(stream for stream in data["streams"] if stream["codec_type"] == "video")
            return VideoMetadata(
                width=int(video["width"]),
                height=int(video["height"]),
                duration=float(data["format"]["duration"]),
            )
        except (KeyError, StopIteration, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RenderingError("El archivo no contiene un video válido.") from exc

    def render(
        self,
        source: Path,
        subtitles: Path,
        output: Path,
        duration: float,
        progress: ProgressCallback | None = None,
    ) -> None:
        if not self.executable:
            raise DependencyError("No se encontró FFmpeg. Instálalo o configura su ubicación.")
        if not source.is_file() or not subtitles.is_file():
            raise RenderingError("No se encontró el video o el archivo de subtítulos.")
        output.parent.mkdir(parents=True, exist_ok=True)
        command = [
            self.executable,
            "-y",
            "-i",
            str(source),
            "-vf",
            f"ass=filename='{_escape_filter_path(subtitles)}'",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-progress",
            "pipe:1",
            "-nostats",
            str(output),
        ]
        self.run(command, duration, progress)

    def run(
        self,
        command: Sequence[str],
        duration: float,
        progress: ProgressCallback | None = None,
    ) -> None:
        with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as errors:
            try:
                process = subprocess.Popen(
                    list(command),
                    stdout=subprocess.PIPE,
                    stderr=errors,
                    text=True,
                    bufsize=1,
                )
                assert process.stdout is not None
                for line in process.stdout:
                    key, separator, value = line.strip().partition("=")
                    if (
                        separator
                        and key == "out_time_us"
                        and value.isdecimal()
                        and progress
                        and duration > 0
                    ):
                        progress(min(1.0, int(value) / 1_000_000 / duration))
                code = process.wait()
            except OSError as exc:
                raise DependencyError("No se pudo ejecutar FFmpeg.") from exc
            errors.seek(0)
            error_detail = errors.read()[-4000:]
        if code:
            logger.error("FFmpeg terminó con código %s: %s", code, error_detail)
            raise RenderingError(
                f"FFmpeg terminó con código {code}; no se pudo generar el video. "
                "Consulta el registro de la aplicación."
            )
        if progress:
            progress(1.0)


def _escape_filter_path(path: Path) -> str:
    """Escapa el valor de archivo para el parser de filtros de FFmpeg."""
    value = str(path.resolve())
    for character in ("\\", "'", ":", ",", "[", "]"):
        value = value.replace(character, "\\" + character)
    return value


def _locate_executable(name: str) -> str | None:
    for prefix in (Path("/opt/homebrew"), Path("/usr/local")):
        full_build = prefix / "opt" / "ffmpeg-full" / "bin" / name
        if full_build.is_file():
            return str(full_build)
    discovered = shutil.which(name)
    if discovered:
        return discovered
    for directory in ("/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin"):
        candidate = Path(directory) / name
        if candidate.is_file():
            return str(candidate)
    return None
