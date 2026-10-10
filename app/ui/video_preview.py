"""Video playback and interactive caption overlay."""

from __future__ import annotations

import logging
from collections.abc import Callable
from math import atan2, degrees, hypot
from uuid import uuid4

from PySide6.QtCore import (
    QEvent,
    QObject,
    QPointF,
    QRectF,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QTextCharFormat,
    QTextLayout,
    QTextOption,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSlider,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from app.core.models import Caption, VideoMetadata
from app.editor.models import EditableCaption
from app.editor.service import CaptionEditorService
from app.styles.registry import StyleRegistry
from app.ui.preview_geometry import (
    Rect as GeometryRect,
)
from app.ui.preview_geometry import (
    caption_handle_at,
    moved_position,
    resized_width,
    scaled_value,
    video_rect,
)

logger = logging.getLogger(__name__)


class CaptionOverlay(QWidget):
    captionSelected = Signal(str)
    captionMoved = Signal(str)
    captionDeleted = Signal(str)
    captionEdited = Signal()

    def __init__(
        self,
        editor: CaptionEditorService,
        metadata: VideoMetadata,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._editor = editor
        self._metadata = metadata
        self._time = 0.0
        self._selected_caption_id: str | None = None
        self._selected_word_index = 0
        self._drag_caption_id: str | None = None
        self._drag_mode = "move"
        self._drag_center = QPointF()
        self._drag_origin = QPointF()
        self._drag_rotation = 0.0
        self._drag_scale = 1.0
        self._drag_width = 0.9
        self._drag_x = 0.0
        self._drag_y = 0.0
        self._drag_token: str | None = None
        self._drag_video_rect = QRectF()
        self._pan = QPointF()
        self._rendered_frame: QWidget | None = None
        self._inline_edit: QLineEdit | None = None
        self._pan_origin = QPointF()
        self._pan_start = QPointF()
        self._draw_text = True
        self._preview_zoom = 1.0
        self._preview_ratio: float | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.installEventFilter(self)

    def set_time(self, seconds: float) -> None:
        self._time = max(0.0, seconds)
        self.update()

    def set_pan(self, x: float, y: float) -> None:
        self._pan = QPointF(x, y)
        self.update()

    def set_rendered_frame(self, rendered_frame: QWidget) -> None:
        self._rendered_frame = rendered_frame

    def set_selected_caption(self, caption_id: str | None) -> None:
        self._selected_caption_id = caption_id
        self.update()

    @property
    def selected_caption_id(self) -> str | None:
        return self._selected_caption_id

    def move_selected_by(self, dx: int, dy: int) -> bool:
        if not self._selected_caption_id:
            return False
        caption = next(
            (
                item
                for item in self._editor.project.captions
                if item.caption_id == self._selected_caption_id
            ),
            None,
        )
        if caption is None:
            return False
        self._editor.move_caption(
            caption.caption_id,
            min(1, max(0, caption.x + dx / self._metadata.width)),
            min(1, max(0, caption.y + dy / self._metadata.height)),
        )
        self.captionMoved.emit(caption.caption_id)
        return True

    def set_selected_word(self, word_index: int) -> None:
        self._selected_word_index = max(0, word_index)
        self.update()

    def set_draw_text(self, enabled: bool) -> None:
        self._draw_text = enabled
        self.update()

    def set_view(self, zoom: float, ratio: float | None) -> None:
        self._preview_zoom = zoom
        self._preview_ratio = ratio
        self.update()

    def paintEvent(self, _event) -> None:
        captions = _active_captions(self._editor.project.captions, self._time)
        if not captions or self._metadata.width <= 0 or self._metadata.height <= 0:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.save()
        if self._preview_ratio is not None:
            painter.setClipRect(
                _preview_viewport_rect(
                    self.width(), self.height(), self._preview_ratio
                )
            )
        video_rect = _preview_video_rect(
            self.width(),
            self.height(),
            self._metadata,
            self._preview_ratio,
            self._preview_zoom,
            self._pan.x(),
            self._pan.y(),
        )
        safe = video_rect.adjusted(
            video_rect.width() * 0.05,
            video_rect.height() * 0.05,
            -video_rect.width() * 0.05,
            -video_rect.height() * 0.05,
        )
        painter.setPen(QPen(QColor(255, 255, 255, 70), 1, Qt.PenStyle.DotLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(safe)
        if self._drag_caption_id and self._drag_mode == "move":
            selected_caption = next(
                (item for item in captions if item.caption_id == self._drag_caption_id),
                None,
            )
            if selected_caption and (
                abs(selected_caption.x - 0.5) < 0.0001
                or abs(selected_caption.y - 0.5) < 0.0001
            ):
                painter.setPen(QPen(QColor("#52D7FF"), 1, Qt.PenStyle.DashLine))
                if abs(selected_caption.x - 0.5) < 0.0001:
                    x = video_rect.x() + video_rect.width() / 2
                    painter.drawLine(QPointF(x, video_rect.top()), QPointF(x, video_rect.bottom()))
                if abs(selected_caption.y - 0.5) < 0.0001:
                    y = video_rect.y() + video_rect.height() / 2
                    painter.drawLine(QPointF(video_rect.left(), y), QPointF(video_rect.right(), y))
        if self._draw_text:
            for caption in captions:
                self._draw_caption(painter, caption, video_rect)
        selected = next(
            (caption for caption in captions if caption.caption_id == self._selected_caption_id),
            None,
        )
        if selected is not None:
            self._draw_selection_handles(
                painter, _caption_bounds(selected, video_rect, self._caption_font(selected))
            )
        painter.restore()

    def _draw_caption(self, painter: QPainter, caption: EditableCaption, video_rect: QRectF) -> None:
        style = self._editor.project.style_config
        scale = video_rect.width() / self._metadata.width * caption.scale
        font = self._caption_font(caption)
        painter.setFont(font)
        text = caption.text.upper() if style.uppercase else caption.text
        text_rect = _caption_bounds(caption, video_rect, font)
        flags = Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap
        if style.shadow_depth > 0:
            painter.setPen(QColor(style.shadow_color))
            painter.drawText(
                text_rect.translated(style.shadow_depth * scale, style.shadow_depth * scale),
                flags,
                text,
            )
        if style.bubble_enabled:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(style.background_color))
            painter.drawRect(text_rect.adjusted(-style.bubble_padding * scale, -style.bubble_padding * scale,
                                                style.bubble_padding * scale, style.bubble_padding * scale))
        painter.setPen(
            QPen(
                QColor(style.outline_color),
                max(0.0, style.outline_width * scale * 2),
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        painter.drawText(text_rect, flags, text)
        _draw_word_colored_text(
            painter,
            text_rect,
            caption,
            style,
            self._time,
            self._selected_word_index,
            caption.style_id or self._editor.project.style_id,
            font,
        )

    def _draw_selection_handles(self, painter: QPainter, bounds: QRectF) -> None:
        expanded = bounds.adjusted(-5, -5, 5, 5)
        painter.setPen(QPen(QColor("#8C75FF"), 2, Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(expanded)
        painter.setBrush(QColor("#8C75FF"))
        for point in (
            expanded.topRight(),
            expanded.bottomLeft(),
        ):
            painter.drawEllipse(point, 6, 6)
        painter.drawEllipse(QPointF(expanded.center().x(), expanded.top()), 6, 6)
        painter.drawEllipse(QPointF(expanded.center().x(), expanded.bottom()), 6, 6)
        delete_center = expanded.topLeft()
        painter.setBrush(QColor("#F05262"))
        painter.drawEllipse(delete_center, 10, 10)
        painter.setPen(QPen(QColor("#FFFFFF"), 2))
        painter.drawLine(
            delete_center + QPointF(-3, -3), delete_center + QPointF(3, 3)
        )
        painter.drawLine(
            delete_center + QPointF(3, -3), delete_center + QPointF(-3, 3)
        )
        rotate_center = expanded.bottomRight()
        painter.setPen(QPen(QColor("#8C75FF"), 2))
        painter.drawLine(
            rotate_center,
            rotate_center + QPointF(0, 18),
        )
        painter.drawEllipse(rotate_center + QPointF(0, 18), 6, 6)

    def _caption_candidates(self) -> list[EditableCaption]:
        return [
            caption
            for caption in self._editor.project.captions
            if caption.start <= self._time < caption.end
        ]

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan_origin = event.position()
            self._pan_start = self._pan
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        candidates = self._caption_candidates()
        if not candidates:
            return
        if self._metadata.width <= 0 or self._metadata.height <= 0:
            return
        if not _preview_viewport_rect(
            self.width(), self.height(), self._preview_ratio
        ).contains(event.position()):
            return
        video_rect = _preview_video_rect(
            self.width(),
            self.height(),
            self._metadata,
            self._preview_ratio,
            self._preview_zoom,
            self._pan.x(),
            self._pan.y(),
        )
        candidates = [
            caption for caption in candidates
            if _caption_bounds(caption, video_rect, self._caption_font(caption))
            .adjusted(-18, -18, 18, 18).contains(event.position())
        ]
        if not candidates:
            return
        caption = next(
            (item for item in reversed(candidates) if item.caption_id == self._selected_caption_id),
            candidates[-1],
        )
        bounds = _caption_bounds(caption, video_rect, self._caption_font(caption)).adjusted(-5, -5, 5, 5)
        delete_bounds = QRectF(
            bounds.topLeft() - QPointF(10, 10),
            bounds.topLeft() + QPointF(10, 10),
        )
        if (
            caption.caption_id == self._selected_caption_id
            and delete_bounds.contains(event.position())
        ):
            self.captionDeleted.emit(caption.caption_id)
            event.accept()
            return
        self._selected_caption_id = caption.caption_id
        self._drag_caption_id = caption.caption_id
        self._drag_center = bounds.center()
        self._drag_origin = event.position()
        self._drag_rotation = caption.rotation_deg
        self._drag_scale = caption.scale
        self._drag_width = caption.max_width
        self._drag_x = caption.x
        self._drag_y = caption.y
        self._drag_video_rect = video_rect
        self._drag_token = str(uuid4())
        handle = caption_handle_at(
            event.position().x(), event.position().y(),
            GeometryRect(bounds.x(), bounds.y(), bounds.width(), bounds.height()),
        )
        if handle == "rotate":
            self._drag_mode = "rotate"
        elif handle == "width":
            self._drag_mode = "width"
        elif handle == "resize":
            self._drag_mode = "resize"
        else:
            self._drag_mode = "move"
        self.captionSelected.emit(caption.caption_id)
        self.update()
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        event.accept()

    def _caption_font(self, caption: EditableCaption) -> QFont:
        style = self._editor.project.style_config
        scale = _preview_video_rect(
            self.width(),
            self.height(),
            self._metadata,
            self._preview_ratio,
            self._preview_zoom,
            self._pan.x(),
            self._pan.y(),
        ).width()
        scale = scale / self._metadata.width * caption.scale
        font = QFont(style.font_family)
        font.setPixelSize(max(8, round(style.font_size * scale)))
        font.setLetterSpacing(
            QFont.SpacingType.AbsoluteSpacing, caption.word_spacing * scale
        )
        return font

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if event.buttons() & Qt.MouseButton.MiddleButton:
            self._pan = self._pan_start + event.position() - self._pan_origin
            if isinstance(self._rendered_frame, RenderedFrameLabel):
                self._rendered_frame.set_pan(self._pan.x(), self._pan.y())
            self.update()
            event.accept()
            return
        if not self._drag_caption_id:
            self._update_cursor(event.position())
            return
        if not self._drag_caption_id or not event.buttons() & Qt.MouseButton.LeftButton:
            return
        video_rect = self._drag_video_rect
        if video_rect.isEmpty():
            return
        if self._drag_mode == "rotate":
            origin_angle = _angle(self._drag_center, self._drag_origin)
            current_angle = _angle(self._drag_center, event.position())
            delta = (current_angle - origin_angle + 180) % 360 - 180
            rotation = max(
                -180.0,
                min(180.0, self._drag_rotation + delta),
            )
            self._editor.rotate_caption(
                self._drag_caption_id, rotation, merge_token=self._drag_token
            )
        elif self._drag_mode == "resize":
            initial = max(1.0, _distance(self._drag_center, self._drag_origin))
            current = _distance(self._drag_center, event.position())
            self._editor.resize_caption(
                self._drag_caption_id,
                scaled_value(self._drag_scale, initial, current),
                merge_token=self._drag_token,
            )
        elif self._drag_mode == "width":
            self._editor.set_caption_width(
                self._drag_caption_id,
                resized_width(
                    self._drag_width,
                    (event.position().x() - self._drag_origin.x())
                    * self._metadata.width
                    / video_rect.width(),
                    self._metadata.width,
                ),
                merge_token=self._drag_token,
            )
        else:
            delta = event.position() - self._drag_origin
            x, y = moved_position(
                self._drag_x,
                self._drag_y,
                delta.x() * self._metadata.width / video_rect.width(),
                delta.y() * self._metadata.height / video_rect.height(),
                self._metadata.width,
                self._metadata.height,
                lock_axis=bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier),
            )
            self._editor.move_caption(
                self._drag_caption_id, x, y, merge_token=self._drag_token
            )
        self.captionMoved.emit(self._drag_caption_id)
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self.unsetCursor()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_caption_id = None
            self._drag_mode = "move"
            self._drag_token = None
            self.captionMoved.emit(self._selected_caption_id or "")
            event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        self.mousePressEvent(event)
        if not self._selected_caption_id:
            return
        caption = next(
            (item for item in self._caption_candidates()
             if item.caption_id == self._selected_caption_id),
            None,
        )
        if caption is None:
            return
        if self._inline_edit is None:
            self._inline_edit = QLineEdit(self)
            self._inline_edit.returnPressed.connect(self._commit_inline_edit)
            self._inline_edit.installEventFilter(self)
        bounds = _caption_bounds(caption, self._drag_video_rect, self._caption_font(caption))
        self._inline_edit.setGeometry(bounds.toAlignedRect())
        self._inline_edit.setText(caption.text)
        self._inline_edit.show()
        self._inline_edit.setFocus()
        self._inline_edit.selectAll()
        event.accept()

    def _commit_inline_edit(self) -> None:
        if self._inline_edit and self._selected_caption_id:
            self._editor.update_text(self._selected_caption_id, self._inline_edit.text())
            self._inline_edit.hide()
            self.captionEdited.emit()

    def eventFilter(self, watched, event) -> bool:
        if (
            watched is self._inline_edit
            and event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key.Key_Escape
        ):
            self._inline_edit.hide()
            return True
        if watched is self and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Tab:
                candidates = self._caption_candidates()
                if candidates:
                    ids = [caption.caption_id for caption in candidates]
                    current = ids.index(self._selected_caption_id) if self._selected_caption_id in ids else -1
                    self._selected_caption_id = ids[(current + 1) % len(ids)]
                    self.captionSelected.emit(self._selected_caption_id)
                    self.update()
                return True
            if self._selected_caption_id and event.key() in {
                Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down
            }:
                step = 10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
                dx = (-step if event.key() == Qt.Key.Key_Left else step if event.key() == Qt.Key.Key_Right else 0)
                dy = (-step if event.key() == Qt.Key.Key_Up else step if event.key() == Qt.Key.Key_Down else 0)
                caption = next(c for c in self._editor.project.captions if c.caption_id == self._selected_caption_id)
                self._editor.move_caption(
                    caption.caption_id,
                    min(1, max(0, caption.x + dx / self._metadata.width)),
                    min(1, max(0, caption.y + dy / self._metadata.height)),
                )
                self.captionMoved.emit(caption.caption_id)
                return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Tab:
            candidates = self._caption_candidates()
            if candidates:
                ids = [caption.caption_id for caption in candidates]
                current = ids.index(self._selected_caption_id) if self._selected_caption_id in ids else -1
                self._selected_caption_id = ids[(current + 1) % len(ids)]
                self.captionSelected.emit(self._selected_caption_id)
                self.update()
            event.accept()
            return
        if self._selected_caption_id and event.key() in {
            Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down
        }:
            step = 10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
            dx = (-step if event.key() == Qt.Key.Key_Left else step if event.key() == Qt.Key.Key_Right else 0)
            dy = (-step if event.key() == Qt.Key.Key_Up else step if event.key() == Qt.Key.Key_Down else 0)
            caption = next(c for c in self._editor.project.captions if c.caption_id == self._selected_caption_id)
            self._editor.move_caption(
                caption.caption_id,
                min(1, max(0, caption.x + dx / self._metadata.width)),
                min(1, max(0, caption.y + dy / self._metadata.height)),
            )
            self.captionMoved.emit(caption.caption_id)
            event.accept()
            return
        super().keyPressEvent(event)

    def _update_cursor(self, position: QPointF) -> None:
        video_rect = _preview_video_rect(
            self.width(), self.height(), self._metadata, self._preview_ratio,
            self._preview_zoom, self._pan.x(), self._pan.y()
        )
        for caption in reversed(self._caption_candidates()):
            bounds = _caption_bounds(caption, video_rect, self._caption_font(caption)).adjusted(-5, -5, 5, 5)
            handle = caption_handle_at(position.x(), position.y(), GeometryRect(bounds.x(), bounds.y(), bounds.width(), bounds.height()))
            if handle:
                self.setCursor(Qt.CursorShape.SizeFDiagCursor if handle == "resize" else Qt.CursorShape.CrossCursor)
                return
            if bounds.contains(position):
                self.setCursor(Qt.CursorShape.SizeAllCursor)
                return
        self.unsetCursor()

    def contextMenuEvent(self, event) -> None:
        self._show_context_menu(event.globalPos())

    def _show_context_menu(self, global_pos) -> None:
        if not self._selected_caption_id:
            return
        menu = QMenu(self)
        center = menu.addAction("Centrar")
        reset = menu.addAction("Restablecer posición y escala")
        duplicate = menu.addAction("Duplicar")
        split = menu.addAction("Dividir aquí")
        delete = menu.addAction("Eliminar")
        chosen = menu.exec(global_pos)
        if chosen == center:
            self._editor.move_caption(self._selected_caption_id, 0.5, 0.5)
        elif chosen == reset:
            self._editor.reset_caption_geometry(self._selected_caption_id)
        elif chosen == duplicate:
            self._selected_caption_id = self._editor.duplicate_caption(self._selected_caption_id)
            self.captionSelected.emit(self._selected_caption_id)
        elif chosen == split:
            caption = next(c for c in self._editor.project.captions if c.caption_id == self._selected_caption_id)
            split_index = next((i for i, word in enumerate(caption.words) if word.start >= self._time), 0)
            if 0 < split_index < len(caption.words):
                self._editor.split_caption(caption.caption_id, split_index)
        elif chosen == delete:
            self.captionDeleted.emit(self._selected_caption_id)
        self.captionEdited.emit()
        self.update()


class PreviewFrameWorker(QObject):
    succeeded = Signal(bytes)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, renderer: Callable[[float], bytes], seconds: float) -> None:
        super().__init__()
        self._renderer = renderer
        self._seconds = seconds

    @Slot()
    def run(self) -> None:
        try:
            self.succeeded.emit(self._renderer(self._seconds))
        except Exception as exc:
            logger.exception("Falló la regeneración del fotograma ASS")
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()


class RenderedFrameLabel(QLabel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._image = QImage()
        self._zoom = 1.0
        self._ratio: float | None = None
        self._pan = QPointF()
        self.setStyleSheet("background: #111318;")

    def set_frame(self, image: QImage) -> None:
        self._image = image

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.update()

    def set_zoom(self, factor: float) -> None:
        self._zoom = max(1.0, min(4.0, factor))
        self.update()

    def set_ratio(self, ratio: float | None) -> None:
        self._ratio = ratio
        self.update()

    def zoom_by(self, factor: float) -> None:
        self.set_zoom(self._zoom * factor)

    @property
    def zoom(self) -> float:
        return self._zoom

    @property
    def ratio(self) -> float | None:
        return self._ratio

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#111318"))
        if self._image.isNull() or self.width() <= 0 or self.height() <= 0:
            return
        target = QRectF(self.rect())
        if self._ratio:
            target_ratio = target.width() / target.height()
            if target_ratio > self._ratio:
                width = target.height() * self._ratio
                target.setX((self.width() - width) / 2)
                target.setWidth(width)
            else:
                height = target.width() / self._ratio
                target.setY((self.height() - height) / 2)
                target.setHeight(height)
        scale_x = target.width() / self._image.width()
        scale_y = target.height() / self._image.height()
        scale = max(scale_x, scale_y) if self._ratio else min(scale_x, scale_y)
        width = self._image.width() * scale * self._zoom
        height = self._image.height() * scale * self._zoom
        destination = QRectF(
            target.center().x() - width / 2 + self._pan.x(),
            target.center().y() - height / 2 + self._pan.y(),
            width,
            height,
        )
        painter.save()
        if self._ratio:
            painter.setClipRect(target)
        painter.drawImage(destination, self._image)
        painter.restore()


class VideoPreview(QWidget):
    captionSelected = Signal(str)
    captionMoved = Signal(str)
    captionDeleted = Signal(str)
    captionEdited = Signal()
    frameError = Signal(str)
    timeChanged = Signal(float)

    def __init__(
        self,
        source: str,
        editor: CaptionEditorService,
        metadata: VideoMetadata,
        parent: QWidget | None = None,
        *,
        frame_renderer: Callable[[float], bytes] | None = None,
    ) -> None:
        super().__init__(parent)
        self._editor = editor
        self._frame_renderer = frame_renderer
        self._frame_thread: QThread | None = None
        self._frame_worker: PreviewFrameWorker | None = None
        self._frame_render_pending = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        video_container = QWidget(self)
        video_container.setObjectName("videoPreview")
        video_stack = QStackedLayout(video_container)
        video_stack.setStackingMode(QStackedLayout.StackingMode.StackAll)
        self._video = QVideoWidget(video_container)
        self._rendered_frame = RenderedFrameLabel(video_container)
        self._rendered_frame.setVisible(frame_renderer is not None)
        self._overlay = CaptionOverlay(editor, metadata, video_container)
        self._overlay.set_rendered_frame(self._rendered_frame)
        self._overlay.set_draw_text(frame_renderer is None)
        self._overlay.captionSelected.connect(self.captionSelected)
        self._overlay.captionMoved.connect(self.captionMoved)
        self._overlay.captionDeleted.connect(self.captionDeleted)
        self._overlay.captionEdited.connect(self.captionEdited)
        video_stack.addWidget(self._video)
        video_stack.addWidget(self._rendered_frame)
        video_stack.addWidget(self._overlay)
        video_stack.setCurrentWidget(self._overlay)
        layout.addWidget(video_container, 1)

        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._player.setAudioOutput(self._audio)
        self._player.setVideoOutput(self._video)
        self._player.positionChanged.connect(self._on_position_changed)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.playbackStateChanged.connect(self._on_playback_state_changed)
        self._player.errorOccurred.connect(self._on_playback_error)
        self._frame_timer = QTimer(self)
        self._frame_timer.setSingleShot(True)
        self._frame_timer.setInterval(220)
        self._frame_timer.timeout.connect(self._start_frame_render)

        controls = QHBoxLayout()
        self._play = QPushButton("▶ Reproducir")
        self._play.clicked.connect(self._toggle_playback)
        self._position = QLabel("00:00 / 00:00")
        self._seek = QSlider(Qt.Orientation.Horizontal)
        self._seek.setRange(0, 0)
        self._seek.sliderMoved.connect(self._player.setPosition)
        controls.addWidget(self._play)
        controls.addWidget(self._seek, 1)
        controls.addWidget(self._position)
        layout.addLayout(controls)
        view_controls = QHBoxLayout()
        self._fit_button = QPushButton("Ajustar")
        zoom_out = QPushButton("−")
        zoom_in = QPushButton("+")
        self._ratio = QComboBox()
        self._ratio.addItem("Original", None)
        self._ratio.addItem("9:16", 9 / 16)
        self._ratio.addItem("1:1", 1.0)
        self._ratio.addItem("16:9", 16 / 9)
        self._fullscreen = QPushButton("Pantalla completa")
        self._fit_button.clicked.connect(self._fit_preview)
        zoom_out.clicked.connect(lambda: self._zoom_preview(0.8))
        zoom_in.clicked.connect(lambda: self._zoom_preview(1.25))
        self._ratio.currentIndexChanged.connect(self._set_preview_ratio)
        self._fullscreen.clicked.connect(self._toggle_fullscreen)
        for control in (self._fit_button, zoom_out, zoom_in, self._ratio, self._fullscreen):
            view_controls.addWidget(control)
        layout.addLayout(view_controls)
        self._player.setSource(QUrl.fromLocalFile(source))
        if frame_renderer is not None:
            self.refresh_frame()

    @property
    def duration(self) -> float:
        return self._player.duration() / 1000

    @property
    def position(self) -> float:
        return self._player.position() / 1000

    @property
    def is_rendering_frame(self) -> bool:
        return self._frame_thread is not None and self._frame_thread.isRunning()

    @property
    def selected_caption_id(self) -> str | None:
        return self._overlay.selected_caption_id

    def move_selected_caption(self, dx: int, dy: int) -> bool:
        return self._overlay.move_selected_by(dx, dy)

    def seek(self, seconds: float) -> None:
        self._player.setPosition(max(0, round(seconds * 1000)))

    def set_selected_caption(self, caption_id: str | None) -> None:
        self._overlay.set_selected_caption(caption_id)
        self.refresh_frame()

    def set_selected_word(self, word_index: int) -> None:
        self._overlay.set_selected_word(word_index)

    def refresh_frame(self) -> None:
        if self._frame_renderer is not None:
            self._frame_render_pending = True
            self._frame_timer.start()

    def _fit_preview(self) -> None:
        self._rendered_frame.set_zoom(1.0)
        self._rendered_frame.set_pan(0, 0)
        self._ratio.setCurrentIndex(0)
        self._overlay.set_view(1.0, None)
        self._overlay.set_pan(0, 0)

    def _zoom_preview(self, factor: float) -> None:
        self._rendered_frame.zoom_by(factor)
        self._overlay.set_view(self._rendered_frame.zoom, self._rendered_frame.ratio)

    @Slot(int)
    def _set_preview_ratio(self, index: int) -> None:
        ratio = self._ratio.itemData(index)
        self._rendered_frame.set_ratio(ratio)
        self._overlay.set_view(self._rendered_frame.zoom, ratio)

    @Slot()
    def _toggle_fullscreen(self) -> None:
        window = self.window()
        if window.isFullScreen():
            window.showNormal()
            self._fullscreen.setText("Pantalla completa")
        else:
            window.showFullScreen()
            self._fullscreen.setText("Salir de pantalla completa")

    def toggle_playback(self) -> None:
        self._toggle_playback()

    @Slot()
    def _toggle_playback(self) -> None:
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    @Slot(int)
    def _on_position_changed(self, position: int) -> None:
        seconds = position / 1000
        self._overlay.set_time(seconds)
        self.timeChanged.emit(seconds)
        self.refresh_frame()
        if not self._seek.isSliderDown():
            self._seek.setValue(position)
        self._update_time_label()

    @Slot(int)
    def _on_duration_changed(self, duration: int) -> None:
        self._seek.setRange(0, max(0, duration))
        self._update_time_label()

    @Slot(QMediaPlayer.PlaybackState)
    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self._play.setText("⏸ Pausar" if playing else "▶ Reproducir")

    @Slot(QMediaPlayer.Error, str)
    def _on_playback_error(self, _error: QMediaPlayer.Error, message: str) -> None:
        self._position.setToolTip(f"No se pudo reproducir el video: {message}")

    @Slot()
    def _start_frame_render(self) -> None:
        if self._frame_renderer is None:
            return
        if self.is_rendering_frame:
            self._frame_render_pending = True
            return
        self._frame_render_pending = False
        thread = QThread(self)
        worker = PreviewFrameWorker(self._frame_renderer, self.position)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(self._on_frame_rendered)
        worker.failed.connect(self._on_frame_error)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_frame_thread_finished)
        self._frame_thread = thread
        self._frame_worker = worker
        thread.start()

    @Slot(bytes)
    def _on_frame_rendered(self, image_data: bytes) -> None:
        image = QImage.fromData(image_data)
        if image.isNull():
            self._on_frame_error("FFmpeg devolvió una imagen de vista previa no válida.")
            return
        self._rendered_frame.setVisible(True)
        self._overlay.set_draw_text(False)
        self._rendered_frame.set_frame(image)
        self._rendered_frame.update()
        self._rendered_frame.setToolTip("")

    @Slot(str)
    def _on_frame_error(self, message: str) -> None:
        self._rendered_frame.setVisible(False)
        self._overlay.set_draw_text(True)
        self._rendered_frame.setToolTip(f"No se pudo actualizar la vista previa: {message}")
        self.frameError.emit(message)

    @Slot()
    def _on_frame_thread_finished(self) -> None:
        self._frame_thread = None
        self._frame_worker = None
        if self._frame_render_pending:
            self._frame_timer.start(0)

    def _update_time_label(self) -> None:
        current = _format_time(self._player.position())
        duration = _format_time(self._player.duration())
        self._position.setText(f"{current} / {duration}")


def _active_caption(
    captions: tuple[Caption | EditableCaption, ...], seconds: float
) -> Caption | EditableCaption | None:
    return next(
        (caption for caption in captions if caption.start <= seconds < caption.end),
        None,
    )


def _active_captions(
    captions: tuple[Caption | EditableCaption, ...], seconds: float
) -> tuple[EditableCaption, ...]:
    return tuple(
        caption
        for caption in captions
        if isinstance(caption, EditableCaption)
        and caption.start <= seconds < caption.end
    )


def _fit_video_rect(width: int, height: int, metadata: VideoMetadata) -> QRectF:
    if width <= 0 or height <= 0 or metadata.width <= 0 or metadata.height <= 0:
        return QRectF()
    video_ratio = metadata.width / metadata.height
    if width / height > video_ratio:
        fitted_width = height * video_ratio
        left = (width - fitted_width) / 2
        return QRectF(left, 0, fitted_width, height)
    fitted_height = width / video_ratio
    top = (height - fitted_height) / 2
    return QRectF(0, top, width, fitted_height)


def _preview_video_rect(
    width: int,
    height: int,
    metadata: VideoMetadata,
    ratio: float | None,
    zoom: float,
    pan_x: float = 0.0,
    pan_y: float = 0.0,
) -> QRectF:
    rect = video_rect(
        width,
        height,
        metadata.width,
        metadata.height,
        zoom=zoom,
        target_ratio=ratio,
        pan_x=pan_x,
        pan_y=pan_y,
    )
    return QRectF(rect.x, rect.y, rect.width, rect.height)


def _preview_viewport_rect(
    width: int, height: int, ratio: float | None
) -> QRectF:
    if width <= 0 or height <= 0:
        return QRectF()
    viewport = QRectF(0, 0, width, height)
    if ratio is None or ratio <= 0:
        return viewport
    if width / height > ratio:
        fitted_width = height * ratio
        return QRectF((width - fitted_width) / 2, 0, fitted_width, height)
    fitted_height = width / ratio
    return QRectF(0, (height - fitted_height) / 2, width, fitted_height)


def _caption_bounds(
    caption: EditableCaption, video_rect: QRectF, font: QFont
) -> QRectF:
    metrics = QFontMetricsF(font)
    max_width = video_rect.width() * caption.max_width
    padding = max(2, font.pixelSize() * 0.25)
    text_width = min(max_width, metrics.horizontalAdvance(caption.text) + 2 * padding)
    line_count = max(1, _wrapped_line_count(caption.text, metrics, max_width - 2 * padding))
    text_height = metrics.lineSpacing() * line_count + 2 * padding
    center = video_rect.topLeft() + QPointF(
        video_rect.width() * caption.x, video_rect.height() * caption.y
    )
    return QRectF(
        center.x() - text_width / 2,
        center.y() - text_height / 2,
        text_width,
        text_height,
    )


def _draw_word_colored_text(
    painter: QPainter,
    text_rect: QRectF,
    caption: EditableCaption,
    style,
    seconds: float,
    selected_word_index: int,
    style_id: str,
    font: QFont,
) -> None:
    behavior = StyleRegistry().get(style_id).behavior
    active_index = next(
        (
            index
            for index, word in enumerate(caption.words)
            if word.start <= seconds < word.end
        ),
        selected_word_index if selected_word_index < len(caption.words) else None,
    )
    if behavior == "one_word" and active_index is not None:
        words = (caption.words[active_index],)
        highlight_index = 0
    else:
        words = caption.words
        highlight_index = active_index
    display_words = tuple(
        word.text.upper() if style.uppercase else word.text for word in words
    )
    text = " ".join(display_words)
    formats = []
    offset = 0
    highlight_enabled = (
        style.highlight_enabled
        if style.highlight_enabled is not None
        else behavior != "one_word"
    )
    for index, word_text in enumerate(display_words):
        if highlight_enabled and index == highlight_index:
            char_format = QTextCharFormat()
            char_format.setForeground(QColor(style.highlight_color))
            format_range = QTextLayout.FormatRange()
            format_range.start = offset
            format_range.length = len(word_text)
            format_range.format = char_format
            formats.append(format_range)
        offset += len(word_text) + 1

    layout = QTextLayout(text, font)
    layout.setFormats(formats)
    text_option = QTextOption()
    text_option.setWrapMode(QTextOption.WrapMode.WordWrap)
    text_option.setAlignment(Qt.AlignmentFlag.AlignHCenter)
    layout.setTextOption(text_option)
    layout.beginLayout()
    line_y = 0.0
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(text_rect.width())
        line.setPosition(QPointF(0, line_y))
        line_y += line.height()
    layout.endLayout()
    layout.draw(
        painter,
        QPointF(text_rect.left(), text_rect.top() + max(0.0, (text_rect.height() - line_y) / 2)),
    )


def _wrapped_line_count(text: str, metrics: QFontMetricsF, max_width: float) -> int:
    return sum(
        _wrapped_word_count(line, metrics, max_width)
        for line in (text.splitlines() or [""])
    )


def _wrapped_word_count(line: str, metrics: QFontMetricsF, max_width: float) -> int:
    line_count = 1
    current = ""
    for word in line.split():
        candidate = f"{current} {word}".strip()
        if current and metrics.horizontalAdvance(candidate) > max_width:
            line_count += 1
            current = word
        else:
            current = candidate
    return line_count


def _format_time(milliseconds: int) -> str:
    seconds = max(0, milliseconds // 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes:02}:{seconds:02}"


def _distance(first: QPointF, second: QPointF) -> float:
    return hypot(second.x() - first.x(), second.y() - first.y())


def _angle(center: QPointF, point: QPointF) -> float:
    return degrees(atan2(point.y() - center.y(), point.x() - center.x()))
