"""Contrato extensible para motores de transcripción."""

from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path

from app.core.models import TranscriptionOptions, TranscriptionResult

ProgressCallback = Callable[[str, float | None], None]


class TranscriptionEngine(ABC):
    @abstractmethod
    def transcribe(
        self,
        media_path: Path,
        options: TranscriptionOptions,
        progress: ProgressCallback | None = None,
    ) -> TranscriptionResult:
        """Transcribe localmente un archivo y devuelve tiempos por palabra."""
