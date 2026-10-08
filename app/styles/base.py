"""Contrato ligero para estilos visuales."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CaptionStyle:
    style_id: str
    display_name: str
    behavior: str
