"""Primitivas ASS reutilizables para animaciones sencillas."""


def pop(duration_ms: int) -> str:
    duration = max(1, duration_ms)
    return rf"{{\fscx85\fscy85\t(0,{duration},\fscx100\fscy100)}}"


def fade_in(duration_ms: int) -> str:
    return rf"{{\fad({max(0, duration_ms)},0)}}"
