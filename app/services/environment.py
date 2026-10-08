"""Diagnóstico explícito de dependencias en el Python de ejecución actual."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from app.core.exceptions import CaptionStudioError
from app.rendering.ffmpeg import FFmpegService

logger = logging.getLogger(__name__)


def active_python_executable() -> str:
    """Devuelve el ejecutable del venv incluso si macOS resuelve sys.executable a Homebrew."""
    if sys.prefix != sys.base_prefix:
        executable_name = "python.exe" if os.name == "nt" else "python"
        virtualenv_python = Path(sys.prefix) / "bin" / executable_name
        if virtualenv_python.is_file():
            return str(virtualenv_python)
    return sys.executable


@dataclass(frozen=True, slots=True)
class DependencyStatus:
    name: str
    available: bool
    details: str
    remedy: str


@dataclass(frozen=True, slots=True)
class EnvironmentReport:
    python_executable: str
    python_version: str
    faster_whisper: DependencyStatus
    ffmpeg: DependencyStatus
    ffprobe: DependencyStatus

    @property
    def ready(self) -> bool:
        return all(
            item.available for item in (self.faster_whisper, self.ffmpeg, self.ffprobe)
        )


class EnvironmentService:
    def __init__(self, ffmpeg: FFmpegService) -> None:
        self._ffmpeg = ffmpeg

    def diagnose(self) -> EnvironmentReport:
        return EnvironmentReport(
            python_executable=active_python_executable(),
            python_version=".".join(map(str, sys.version_info[:3])),
            faster_whisper=self._check_faster_whisper(),
            ffmpeg=self._check_ffmpeg(),
            ffprobe=self._check_ffprobe(),
        )

    @staticmethod
    def _check_faster_whisper() -> DependencyStatus:
        try:
            installed = importlib.util.find_spec("faster_whisper") is not None
            version = importlib.metadata.version("faster-whisper") if installed else None
        except (ImportError, ValueError, importlib.metadata.PackageNotFoundError) as exc:
            logger.warning("Error consultando faster-whisper: %s", exc)
            installed, version = False, None
        if not installed:
            try:
                result = subprocess.run(
                    [active_python_executable(), "-c", "import faster_whisper"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=15,
                )
                installed = result.returncode == 0
                if installed:
                    version = importlib.metadata.version("faster-whisper")
            except (OSError, subprocess.TimeoutExpired, importlib.metadata.PackageNotFoundError) as exc:
                logger.warning(
                    "No se pudo verificar faster-whisper con %s: %s",
                    active_python_executable(),
                    exc,
                )
        return DependencyStatus(
            name="faster-whisper",
            available=installed,
            details=f"Instalado · {version}" if installed else "Módulo no encontrado",
            remedy=(
                "Instala con este intérprete: "
                f'"{active_python_executable()}" -m pip install faster-whisper'
            ),
        )

    def _check_ffmpeg(self) -> DependencyStatus:
        executable = self._ffmpeg.executable or shutil.which("ffmpeg")
        if not executable:
            return DependencyStatus(
                "FFmpeg", False, "No está disponible", "Instala FFmpeg con soporte libass."
            )
        try:
            result = subprocess.run(
                [executable, "-version"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except (CaptionStudioError, OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("No se pudo validar FFmpeg: %s", exc)
            return DependencyStatus(
                "FFmpeg",
                False,
                f"No se pudo ejecutar desde {executable}.",
                "Instala `brew install ffmpeg-full`.",
            )
        if result.returncode:
            return DependencyStatus(
                "FFmpeg", False, "No se pudo iniciar", "Instala `brew install ffmpeg-full`."
            )
        try:
            filters = subprocess.run(
                [executable, "-hide_banner", "-filters"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("No se pudo verificar el filtro ASS de FFmpeg: %s", exc)
            return DependencyStatus("FFmpeg", False, "No se pudo comprobar libass", "Instala ffmpeg-full.")
        if filters.returncode or not any(
            len(columns := line.split()) > 1 and columns[1] == "ass"
            for line in filters.stdout.splitlines()
        ):
            return DependencyStatus(
                "FFmpeg", False, "No incluye filtro ASS/libass", "Instala `brew install ffmpeg-full`."
            )
        version = result.stdout.splitlines()[0].removeprefix("ffmpeg version ")
        return DependencyStatus(
            "FFmpeg", True, f"{version.split()[0]} · {executable}", ""
        )

    def _check_ffprobe(self) -> DependencyStatus:
        executable = self._ffmpeg.probe or shutil.which("ffprobe")
        if not executable:
            return DependencyStatus(
                "ffprobe", False, "No está disponible", "Instala FFmpeg con ffprobe."
            )
        try:
            result = subprocess.run(
                [executable, "-version"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("No se pudo validar ffprobe: %s", exc)
            return DependencyStatus(
                "ffprobe", False, "No se pudo ejecutar", "Reinstala FFmpeg."
            )
        if result.returncode:
            return DependencyStatus(
                "ffprobe", False, "No se pudo iniciar", "Reinstala FFmpeg."
            )
        version = result.stdout.splitlines()[0].removeprefix("ffprobe version ")
        return DependencyStatus("ffprobe", True, f"{version.split()[0]} · {executable}", "")
