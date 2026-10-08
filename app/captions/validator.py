"""Validaciones comunes para exportadores."""

from app.core.models import Caption


def validate_captions(captions: tuple[Caption, ...]) -> None:
    previous_start = -1.0
    for caption in captions:
        if caption.start < previous_start:
            raise ValueError("Los subtítulos deben estar ordenados cronológicamente.")
        previous_start = caption.start
