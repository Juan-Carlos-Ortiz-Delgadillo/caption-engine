"""Video playback and interactive caption overlay."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QUrl, Signal, Slot
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
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
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from app.core.models import Caption, VideoMetadata
from app.editor.service import CaptionEditorService
from app.editor.models import EditableCaption
from app.styles.registry import StyleRegistry


class CaptionOverlay(QWidget):
    captionSelected = Signal(str)
    captionMoved = Signal(str)

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
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def set_time(self, seconds: float) -> None:
        self._time = max(0.0, seconds)
        self.update()

    def set_selected_caption(self, caption_id: str | None) -> None:
        self._selected_caption_id = caption_id
        self.update()

    def set_selected_word(self, word_index: int) -> None:
        self._selected_word_index = max(0, word_index)
        self.update()

    def paintEvent(self, _event) -> None:
        caption = _active_caption(self._editor.project.captions, self._time)
        if caption is None or self._metadata.width <= 0 or self._metadata.height <= 0:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        video_rect = _fit_video_rect(self.width(), self.height(), self._metadata)
        style = self._editor.project.style_config
        scale = video_rect.width() / self._metadata.width * caption.scale
        font = QFont(style.font_family)
        font.setPixelSize(max(8, round(style.font_size * scale)))
        font.setLetterSpacing(
            QFont.SpacingType.AbsoluteSpacing, caption.word_spacing * scale
        )
        painter.setFont(font)

        text = caption.text.upper() if style.uppercase else caption.text
        text_rect = _caption_bounds(caption, video_rect, font)
        flags = Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap

        if style.shadow_depth > 0:
            shadow = text_rect.translated(
                style.shadow_depth * scale, style.shadow_depth * scale
            )
            painter.setPen(QColor(style.shadow_color))
            painter.drawText(shadow, flags, text)
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
            self._editor.project.style_id,
            font,
        )
        if caption.caption_id == self._selected_caption_id:
            painter.setPen(QPen(QColor("#8C75FF"), 2, Qt.PenStyle.DashLine))
            painter.drawRect(text_rect.adjusted(-5, -5, 5, 5))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        caption = _active_caption(self._editor.project.captions, self._time)
        if caption is None:
            return
        if self._metadata.width <= 0 or self._metadata.height <= 0:
            return
        video_rect = _fit_video_rect(self.width(), self.height(), self._metadata)
        if not _caption_bounds(caption, video_rect, self._caption_font(caption)).adjusted(
            -10, -10, 10, 10
        ).contains(event.position()):
            return
        self._selected_caption_id = caption.caption_id
        self._drag_caption_id = caption.caption_id
        self.captionSelected.emit(caption.caption_id)
        self.update()
        event.accept()

    def _caption_font(self, caption: EditableCaption) -> QFont:
        style = self._editor.project.style_config
        scale = _fit_video_rect(self.width(), self.height(), self._metadata).width()
        scale = scale / self._metadata.width * caption.scale
        font = QFont(style.font_family)
        font.setPixelSize(max(8, round(style.font_size * scale)))
        font.setLetterSpacing(
            QFont.SpacingType.AbsoluteSpacing, caption.word_spacing * scale
        )
        return font

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self._drag_caption_id or not event.buttons() & Qt.MouseButton.LeftButton:
            return
        video_rect = _fit_video_rect(self.width(), self.height(), self._metadata)
        if video_rect.isEmpty():
            return
        x = min(1.0, max(0.0, (event.position().x() - video_rect.x()) / video_rect.width()))
        y = min(1.0, max(0.0, (event.position().y() - video_rect.y()) / video_rect.height()))
        self._editor.move_caption(self._drag_caption_id, x, y)
        self.captionMoved.emit(self._drag_caption_id)
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_caption_id = None
            event.accept()


class VideoPreview(QWidget):
    captionSelected = Signal(str)
    captionMoved = Signal(str)
    timeChanged = Signal(float)

    def __init__(
        self,
        source: str,
        editor: CaptionEditorService,
        metadata: VideoMetadata,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._editor = editor

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        video_container = QWidget(self)
        video_container.setObjectName("videoPreview")
        video_stack = QStackedLayout(video_container)
        video_stack.setStackingMode(QStackedLayout.StackingMode.StackAll)
        self._video = QVideoWidget(video_container)
        self._overlay = CaptionOverlay(editor, metadata, video_container)
        self._overlay.captionSelected.connect(self.captionSelected)
        self._overlay.captionMoved.connect(self.captionMoved)
        video_stack.addWidget(self._video)
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
        self._player.setSource(QUrl.fromLocalFile(source))

    @property
    def duration(self) -> float:
        return self._player.duration() / 1000

    @property
    def position(self) -> float:
        return self._player.position() / 1000

    def seek(self, seconds: float) -> None:
        self._player.setPosition(max(0, round(seconds * 1000)))

    def set_selected_caption(self, caption_id: str | None) -> None:
        self._overlay.set_selected_caption(caption_id)

    def set_selected_word(self, word_index: int) -> None:
        self._overlay.set_selected_word(word_index)

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


def _caption_bounds(
    caption: EditableCaption, video_rect: QRectF, font: QFont
) -> QRectF:
    metrics = QFontMetricsF(font)
    max_width = video_rect.width() * 0.9
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
    lines = 1
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and metrics.horizontalAdvance(candidate) > max_width:
            lines += 1
            current = word
        else:
            current = candidate
    return lines


def _format_time(milliseconds: int) -> str:
    seconds = max(0, milliseconds // 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes:02}:{seconds:02}"
