"""Non-destructive editor data models independent of Qt."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from math import isfinite
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.models import (
    Caption,
    CaptionStyleConfig,
    TranscriptSegment,
    TranscriptionOptions,
    TranscriptionResult,
    VideoMetadata,
    Word,
    result_to_dict,
)


@dataclass(frozen=True, slots=True)
class EditableCaption:
    start: float
    end: float
    words: tuple[Word, ...]
    caption_id: str = field(default_factory=lambda: str(uuid4()))
    x: float = 0.5
    y: float = 0.82
    scale: float = 1.0
    word_spacing: float = 0.0
    style_id: str | None = None

    def __post_init__(self) -> None:
        if not self.caption_id or not self.words:
            raise ValueError("El caption debe tener un identificador y al menos una palabra.")
        if not isfinite(self.start) or not isfinite(self.end) or self.start < 0 or self.end <= self.start:
            raise ValueError("El tiempo del caption no es válido.")
        if not 0 <= self.x <= 1 or not 0 <= self.y <= 1:
            raise ValueError("La posición normalizada debe estar entre 0 y 1.")
        if not isfinite(self.scale) or not 0.25 <= self.scale <= 4:
            raise ValueError("La escala debe estar entre 0.25 y 4.")
        if not isfinite(self.word_spacing) or not -20 <= self.word_spacing <= 100:
            raise ValueError("El espaciado entre palabras debe estar entre -20 y 100.")
        if any(
            not isfinite(word.start)
            or not isfinite(word.end)
            or word.start < 0
            or word.end < word.start
            or word.confidence is not None
            and not isfinite(word.confidence)
            for word in self.words
        ):
            raise ValueError("Las palabras deben tener timestamps finitos y no negativos.")

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.words)

    def to_caption(self) -> Caption:
        return Caption(self.start, self.end, self.words)


@dataclass(frozen=True, slots=True)
class CaptionEditorProject:
    source_video: Path
    original_transcript: TranscriptionResult
    captions: tuple[EditableCaption, ...]
    style_id: str
    style_config: CaptionStyleConfig
    edited: bool = False
    transcription_options: TranscriptionOptions = field(default_factory=TranscriptionOptions)
    video_metadata: VideoMetadata | None = None

    def __post_init__(self) -> None:
        ids = [caption.caption_id for caption in self.captions]
        if len(ids) != len(set(ids)):
            raise ValueError("Los identificadores de caption deben ser únicos.")
        if not self.style_id:
            raise ValueError("El proyecto debe tener un estilo.")
        if self.video_metadata is not None and (
            self.video_metadata.width < 1
            or self.video_metadata.height < 1
            or not isfinite(self.video_metadata.duration)
            or self.video_metadata.duration <= 0
        ):
            raise ValueError("Los datos del video no son válidos.")

    @classmethod
    def create(
        cls,
        source_video: Path,
        original_transcript: TranscriptionResult,
        captions: tuple[Caption, ...],
        style_id: str,
        style_config: CaptionStyleConfig,
        transcription_options: TranscriptionOptions = TranscriptionOptions(),
        video_metadata: VideoMetadata | None = None,
    ) -> CaptionEditorProject:
        return cls(
            source_video=source_video,
            original_transcript=original_transcript,
            captions=tuple(
                EditableCaption(
                    caption.start,
                    caption.end,
                    caption.words,
                    x=0.5,
                    y={"top": 0.12, "center": 0.5, "bottom": 0.82}[
                        style_config.position
                    ],
                )
                for caption in captions
            ),
            style_id=style_id,
            style_config=style_config,
            transcription_options=transcription_options,
            video_metadata=video_metadata,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 2,
            "source_video": str(self.source_video),
            "transcript": result_to_dict(self.original_transcript),
            "captions": [
                {
                    "id": caption.caption_id,
                    "start": caption.start,
                    "end": caption.end,
                    "words": [asdict(word) for word in caption.words],
                    "position": {"x": caption.x, "y": caption.y},
                    "scale": caption.scale,
                    "word_spacing": caption.word_spacing,
                    "style_id": caption.style_id,
                }
                for caption in self.captions
            ],
            "style_id": self.style_id,
            "style_config": asdict(self.style_config),
            "transcription": asdict(self.transcription_options),
            "video_metadata": (
                asdict(self.video_metadata) if self.video_metadata is not None else None
            ),
            "edited": self.edited,
        }


def transcript_from_dict(data: dict[str, Any]) -> TranscriptionResult:
    return TranscriptionResult(
        language=data["language"],
        segments=tuple(
            TranscriptSegment(
                start=segment["start"],
                end=segment["end"],
                words=tuple(Word(**word) for word in segment["words"]),
            )
            for segment in data["segments"]
        ),
        language_probability=data.get("language_probability"),
    )


def project_from_dict(data: dict[str, Any]) -> CaptionEditorProject:
    if data.get("version") != 2:
        raise ValueError("La versión del proyecto editable no es compatible.")
    transcript = transcript_from_dict(data["transcript"])
    captions = tuple(
        EditableCaption(
            caption_id=caption["id"],
            start=caption["start"],
            end=caption["end"],
            words=tuple(Word(**word) for word in caption["words"]),
            x=caption["position"]["x"],
            y=caption["position"]["y"],
            scale=caption.get("scale", 1.0),
            word_spacing=caption.get("word_spacing", 0.0),
            style_id=caption.get("style_id"),
        )
        for caption in data["captions"]
    )
    return CaptionEditorProject(
        source_video=Path(data["source_video"]),
        original_transcript=transcript,
        captions=captions,
        style_id=data["style_id"],
        style_config=CaptionStyleConfig(**data["style_config"]),
        edited=bool(data.get("edited", False)),
        transcription_options=TranscriptionOptions(**data.get("transcription", {})),
        video_metadata=(
            VideoMetadata(**data["video_metadata"])
            if data.get("video_metadata") is not None
            else None
        ),
    )
