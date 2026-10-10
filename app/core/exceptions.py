"""Excepciones presentables y errores de infraestructura."""


class CaptionStudioError(Exception):
    """Error esperado que puede comunicarse directamente al usuario."""


class DependencyError(CaptionStudioError):
    """Falta una herramienta o dependencia necesaria."""


class TranscriptionError(CaptionStudioError):
    """No se pudo transcribir el medio."""


class CaptionLayoutError(CaptionStudioError):
    """El texto no cabe en el área de salida con la fuente configurada."""


class RenderingError(CaptionStudioError):
    """No se pudo generar el video de salida."""
