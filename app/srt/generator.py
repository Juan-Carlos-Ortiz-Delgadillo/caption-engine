"""Generador SRT estándar, sin estilos específicos de ASS."""

from app.captions.validator import validate_captions
from app.core.models import Caption


class SRTGenerator:
    def generate(self, captions: tuple[Caption, ...]) -> str:
        validate_captions(captions)
        blocks = [
            f"{index}\n{_timestamp(caption.start)} --> {_timestamp(caption.end)}\n"
            f"{caption.text}"
            for index, caption in enumerate(captions, 1)
        ]
        return "\n\n".join(blocks) + ("\n" if blocks else "")


def _timestamp(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{whole_seconds:02},{milliseconds:03}"
