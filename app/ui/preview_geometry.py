"""Pure coordinate transforms for the caption preview."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Rect:
    x: float
    y: float
    width: float
    height: float

    @property
    def center_x(self) -> float:
        return self.x + self.width / 2

    @property
    def center_y(self) -> float:
        return self.y + self.height / 2

    def contains(self, x: float, y: float) -> bool:
        return self.x <= x <= self.x + self.width and self.y <= y <= self.y + self.height


def video_rect(
    widget_width: float,
    widget_height: float,
    video_width: float,
    video_height: float,
    *,
    zoom: float = 1.0,
    target_ratio: float | None = None,
    pan_x: float = 0.0,
    pan_y: float = 0.0,
) -> Rect:
    """Return the displayed video bounds in widget coordinates.

    A target ratio uses a cover fit (cropping); original ratio uses contain
    (letterboxing). Zoom is centered on the viewport and pan is in widget px.
    """
    if min(widget_width, widget_height, video_width, video_height, zoom) <= 0:
        return Rect(0, 0, 0, 0)

    viewport = Rect(0, 0, widget_width, widget_height)
    if target_ratio is not None:
        if target_ratio <= 0:
            return Rect(0, 0, 0, 0)
        if widget_width / widget_height > target_ratio:
            viewport_width = widget_height * target_ratio
            viewport = Rect((widget_width - viewport_width) / 2, 0, viewport_width, widget_height)
        else:
            viewport_height = widget_width / target_ratio
            viewport = Rect(0, (widget_height - viewport_height) / 2, widget_width, viewport_height)

    scale_x = viewport.width / video_width
    scale_y = viewport.height / video_height
    scale = max(scale_x, scale_y) if target_ratio is not None else min(scale_x, scale_y)
    displayed_width = video_width * scale * zoom
    displayed_height = video_height * scale * zoom
    return Rect(
        viewport.center_x - displayed_width / 2 + pan_x,
        viewport.center_y - displayed_height / 2 + pan_y,
        displayed_width,
        displayed_height,
    )


def widget_to_video(
    x: float, y: float, displayed: Rect, video_width: float, video_height: float
) -> tuple[float, float] | None:
    if displayed.width <= 0 or displayed.height <= 0 or not displayed.contains(x, y):
        return None
    return (
        (x - displayed.x) * video_width / displayed.width,
        (y - displayed.y) * video_height / displayed.height,
    )


def video_to_widget(
    x: float, y: float, displayed: Rect, video_width: float, video_height: float
) -> tuple[float, float]:
    if video_width <= 0 or video_height <= 0:
        raise ValueError("La resolución de video debe ser positiva.")
    return (
        displayed.x + x * displayed.width / video_width,
        displayed.y + y * displayed.height / video_height,
    )


def hit_test_caption(
    x: float, y: float, bounds: Rect, *, tolerance: float = 0.0
) -> bool:
    return Rect(
        bounds.x - tolerance,
        bounds.y - tolerance,
        bounds.width + tolerance * 2,
        bounds.height + tolerance * 2,
    ).contains(x, y)


def caption_handle_at(
    x: float, y: float, bounds: Rect, *, radius: float = 10.0
) -> str | None:
    handles = (
        ("resize", bounds.x + bounds.width, bounds.y),
        ("width", bounds.center_x, bounds.y),
        ("width", bounds.center_x, bounds.y + bounds.height),
        ("rotate", bounds.x + bounds.width, bounds.y + bounds.height + 18),
    )
    for name, handle_x, handle_y in handles:
        if (x - handle_x) ** 2 + (y - handle_y) ** 2 <= radius**2:
            return name
    return None


def moved_position(
    start_x: float,
    start_y: float,
    delta_x: float,
    delta_y: float,
    video_width: float,
    video_height: float,
    *,
    lock_axis: bool = False,
    snap_pixels: float = 8.0,
) -> tuple[float, float]:
    if min(video_width, video_height) <= 0:
        raise ValueError("La resolución de video debe ser positiva.")
    if lock_axis:
        if abs(delta_x) >= abs(delta_y):
            delta_y = 0
        else:
            delta_x = 0
    x = start_x + delta_x / video_width
    y = start_y + delta_y / video_height
    if abs(x - 0.5) * video_width <= snap_pixels:
        x = 0.5
    if abs(y - 0.5) * video_height <= snap_pixels:
        y = 0.5
    return min(1.0, max(0.0, x)), min(1.0, max(0.0, y))


def scaled_value(
    initial_scale: float, initial_distance: float, current_distance: float
) -> float:
    if initial_distance <= 0:
        return initial_scale
    return min(4.0, max(0.25, initial_scale * current_distance / initial_distance))


def resized_width(
    initial_fraction: float,
    delta_x: float,
    video_width: float,
) -> float:
    if video_width <= 0:
        raise ValueError("La resolución de video debe ser positiva.")
    return min(1.0, max(0.1, initial_fraction + 2 * delta_x / video_width))
