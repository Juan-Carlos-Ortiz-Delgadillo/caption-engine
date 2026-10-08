"""Adaptador lazy de faster-whisper, sin filtrar sus objetos fuera del motor."""

import logging
from pathlib import Path
from typing import Any

from app.core.exceptions import DependencyError, TranscriptionError
from app.core.models import TranscriptSegment, TranscriptionOptions, TranscriptionResult, Word
from app.transcription.base import ProgressCallback, TranscriptionEngine

logger = logging.getLogger(__name__)


class FasterWhisperEngine(TranscriptionEngine):
    def __init__(self) -> None:
        self._models: dict[tuple[str, str, str], Any] = {}

    def transcribe(
        self,
        media_path: Path,
        options: TranscriptionOptions,
        progress: ProgressCallback | None = None,
    ) -> TranscriptionResult:
        if not media_path.is_file():
            raise TranscriptionError(f"No se encontró el video: {media_path.name}")
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise DependencyError(
                "No está instalado faster-whisper. Instala las dependencias del proyecto."
            ) from exc
        try:
            if progress:
                progress(f"Cargando modelo Whisper {options.model}…", None)
            key = (options.model, options.device, options.compute_type)
            model = self._models.get(key)
            if model is None:
                model = WhisperModel(
                    options.model, device=options.device, compute_type=options.compute_type
                )
                self._models[key] = model
            segments, info = model.transcribe(
                str(media_path),
                language=options.language or None,
                beam_size=options.beam_size,
                word_timestamps=True,
                vad_filter=options.vad_filter,
            )
            converted: list[TranscriptSegment] = []
            for segment in segments:
                if progress and info.duration:
                    progress(
                        "Transcribiendo audio…",
                        min(100.0, float(segment.end) / float(info.duration) * 100),
                    )
                words = tuple(
                    Word(
                        text=word.word.strip(),
                        start=max(0.0, float(word.start)),
                        end=max(float(word.start), float(word.end)),
                        confidence=float(word.probability)
                        if word.probability is not None
                        else None,
                    )
                    for word in (segment.words or [])
                    if word.word.strip()
                )
                if words:
                    converted.append(
                        TranscriptSegment(
                            start=words[0].start,
                            end=words[-1].end,
                            words=words,
                        )
                    )
            if not converted:
                raise TranscriptionError(
                    "No se detectó voz con marcas de tiempo. Prueba otro archivo o modelo."
                )
            return TranscriptionResult(
                language=info.language,
                segments=tuple(converted),
                language_probability=float(info.language_probability)
                if info.language_probability is not None
                else None,
            )
        except TranscriptionError:
            raise
        except Exception as exc:
            logger.exception(
                "Falló la transcripción del archivo %s con el modelo %s",
                media_path.name,
                options.model,
            )
            raise TranscriptionError(
                f"No se pudo cargar el modelo «{options.model}» o transcribir el video. "
                "Revisa tu conexión si el modelo aún no se descargó y el espacio disponible."
            ) from exc
