"""Interactive caption timeline with selection, seeking, and trim handles."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QWidget

from app.editor.service import CaptionEditorService


class TimelineEditor(QWidget):
    captionSelected = Signal(str)
    seekRequested = Signal(float)
    timingChanged = Signal(str, float, float)
    viewChanged = Signal(float, float)

    _BLOCK_TOP = 43
    _BLOCK_HEIGHT = 42
    _HANDLE_WIDTH = 8

    def __init__(
        self,
        editor: CaptionEditorService,
        duration: float,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._editor = editor
        self._duration = max(0.1, duration)
        self._current_time = 0.0
        self._selected_caption_id: str | None = None
        self._view_duration = self._duration
        self._view_start = 0.0
        self._drag_id: str | None = None
        self._drag_mode: str | None = None
        self._drag_origin = 0.0
        self._drag_start = 0.0
        self._drag_end = 0.0
        self.setMinimumHeight(108)
        self.setMouseTracking(True)

    def set_duration(self, duration: float) -> None:
        self._duration = max(0.1, duration)
        self.fit()

    def set_current_time(self, seconds: float) -> None:
        self._current_time = min(self._duration, max(0.0, seconds))
        if (
            self._current_time < self._view_start
            or self._current_time > self._view_start + self._view_duration
        ):
            self.set_view_start(self._current_time - self._view_duration / 2)
        self.update()

    def set_selected_caption(self, caption_id: str | None) -> None:
        self._selected_caption_id = caption_id
        self.update()

    def refresh(self) -> None:
        self.update()

    def zoom(self, factor: float) -> None:
        center = self._view_start + self._view_duration / 2
        self._view_duration = min(
            self._duration, max(0.5, self._view_duration / factor)
        )
        self._view_start = min(
            max(0.0, center - self._view_duration / 2),
            max(0.0, self._duration - self._view_duration),
        )
        self.viewChanged.emit(self._view_start, self._view_duration)
        self.update()

    def fit(self) -> None:
        self._view_duration = self._duration
        self._view_start = 0.0
        self.viewChanged.emit(self._view_start, self._view_duration)
        self.update()

    def set_view_start(self, seconds: float) -> None:
        self._view_start = min(
            max(0.0, seconds),
            max(0.0, self._duration - self._view_duration),
        )
        self.viewChanged.emit(self._view_start, self._view_duration)
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#1d2027"))
        pixels_per_second = self.width() / self._view_duration
        tick_step = _tick_step(self._view_duration)
        first_tick = int(self._view_start / tick_step) * tick_step
        tick = first_tick
        while tick <= self._view_start + self._view_duration:
            x = (tick - self._view_start) * pixels_per_second
            painter.setPen(QPen(QColor("#515662"), 1))
            painter.drawLine(round(x), 27, round(x), self.height())
            painter.setPen(QColor("#aeb5c2"))
            painter.drawText(round(x + 4), 18, _time_label(tick))
            tick += tick_step

        for caption in self._editor.project.captions:
            if caption.end < self._view_start or caption.start > self._view_start + self._view_duration:
                continue
            left = (caption.start - self._view_start) * pixels_per_second
            right = (caption.end - self._view_start) * pixels_per_second
            block = QRectF(left, self._BLOCK_TOP, max(10.0, right - left), self._BLOCK_HEIGHT)
            selected = caption.caption_id == self._selected_caption_id
            border = (
                "#ff6b6b"
                if caption.layout_warning
                else "#b7aaff"
                if selected
                else "#6b5bd4"
            )
            painter.setPen(QPen(QColor(border), 2 if caption.layout_warning else 1))
            painter.setBrush(QColor("#5946b9" if selected else "#393354"))
            painter.drawRoundedRect(block, 6, 6)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(
                block.adjusted(8, 0, -20 if caption.layout_warning else -8, 0),
                Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextSingleLine,
                caption.text,
            )
            if caption.layout_warning:
                painter.setPen(QColor("#ff7777"))
                painter.drawText(
                    block.adjusted(0, 0, -5, 0),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    "!",
                )

        playhead_x = (self._current_time - self._view_start) * pixels_per_second
        if 0 <= playhead_x <= self.width():
            painter.setPen(QPen(QColor("#ff5d68"), 2))
            painter.drawLine(round(playhead_x), 24, round(playhead_x), self.height())

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        seconds = self._x_to_time(event.position().x())
        caption = self._caption_at(event.position().x(), event.position().y())
        if caption is None:
            self.seekRequested.emit(seconds)
            return
        self._selected_caption_id = caption.caption_id
        self.captionSelected.emit(caption.caption_id)
        self._drag_id = caption.caption_id
        self._drag_origin = seconds
        self._drag_start = caption.start
        self._drag_end = caption.end
        left = (caption.start - self._view_start) * self.width() / self._view_duration
        right = (caption.end - self._view_start) * self.width() / self._view_duration
        if abs(event.position().x() - left) <= self._HANDLE_WIDTH:
            self._drag_mode = "start"
        elif abs(event.position().x() - right) <= self._HANDLE_WIDTH:
            self._drag_mode = "end"
        else:
            self._drag_mode = "move"
        self.update()
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self._drag_id or not event.buttons() & Qt.MouseButton.LeftButton:
            return
        seconds = self._x_to_time(event.position().x())
        delta = seconds - self._drag_origin
        if self._drag_mode == "start":
            start = min(self._drag_end - 0.05, max(0.0, self._drag_start + delta))
            end = self._drag_end
        elif self._drag_mode == "end":
            start = self._drag_start
            end = max(self._drag_start + 0.05, min(self._duration, self._drag_end + delta))
        else:
            duration = self._drag_end - self._drag_start
            start = min(
                max(0.0, self._drag_start + delta),
                max(0.0, self._duration - duration),
            )
            end = start + duration
        self.timingChanged.emit(self._drag_id, start, end)
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_id = None
            self._drag_mode = None

    def _x_to_time(self, x: float) -> float:
        return min(
            self._duration,
            max(0.0, self._view_start + x / max(1, self.width()) * self._view_duration),
        )

    def _caption_at(self, x: float, y: float):
        if not self._BLOCK_TOP <= y <= self._BLOCK_TOP + self._BLOCK_HEIGHT:
            return None
        seconds = self._x_to_time(x)
        return next(
            (
                caption
                for caption in reversed(self._editor.project.captions)
                if caption.start <= seconds <= caption.end
            ),
            None,
        )


def _tick_step(duration: float) -> float:
    target = duration / 8
    steps = (0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300)
    return next((step for step in steps if step >= target), steps[-1])


def _time_label(seconds: float) -> str:
    total = max(0, round(seconds))
    minutes, seconds = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes:02}:{seconds:02}"
