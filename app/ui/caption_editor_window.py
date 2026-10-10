"""Visual caption editing window."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, replace
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QCloseEvent, QColor, QFont, QFontDatabase, QKeyEvent, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFontComboBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.core.models import VideoMetadata
from app.editor.service import CaptionEditorService
from app.services.pipeline import CaptionApplicationService, PipelineResult
from app.styles.registry import StyleRegistry
from app.ui.timeline_editor import TimelineEditor
from app.ui.video_preview import VideoPreview

logger = logging.getLogger(__name__)


class EditorRenderWorker(QObject):
    progress = Signal(str, int)
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        service: CaptionApplicationService,
        editor: CaptionEditorService,
        metadata: VideoMetadata,
        output_directory: Path,
        output_name: str,
        crf: int,
    ) -> None:
        super().__init__()
        self._service = service
        self._project = editor.project
        self._metadata = metadata
        self._output_directory = output_directory
        self._output_name = output_name
        self._crf = crf

    @Slot()
    def run(self) -> None:
        try:
            result = self._service.export_editor_project(
                self._project,
                self._metadata,
                self._output_directory,
                output_name=self._output_name,
                crf=self._crf,
                progress=self.progress.emit,
            )
            self.succeeded.emit(result)
        except Exception:
            logger.exception("Falló el render del proyecto editable")
            self.failed.emit("No se pudo renderizar el video. Revisa el registro de la aplicación.")
        finally:
            self.finished.emit()


class CaptionEditorWindow(QMainWindow):
    def __init__(
        self,
        editor: CaptionEditorService,
        metadata: VideoMetadata,
        project_path: Path,
        export_service: CaptionApplicationService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._editor = editor
        self._export_service = export_service
        self._metadata = metadata
        self._project_path = project_path
        self._render_thread: QThread | None = None
        self._render_worker: EditorRenderWorker | None = None
        captions = self._editor.project.captions
        self._selected_caption_id = captions[0].caption_id if captions else None
        self._selected_word_index = 0
        self._updating_controls = False
        self._missing_font = False
        self.setWindowTitle("Caption Editor · Animated Caption Studio")
        self.setMinimumSize(1040, 680)
        self.resize(1280, 820)

        root = QWidget(self)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        heading = QHBoxLayout()
        title = QLabel("Caption Editor")
        title.setObjectName("title")
        heading.addWidget(title)
        heading.addStretch(1)
        self._save_status = QLabel("Guardado")
        self._save_button = QPushButton("Guardar proyecto")
        self._save_button.clicked.connect(self._save_project)
        self._export_ass_button = QPushButton("Exportar ASS/SRT")
        self._export_ass_button.clicked.connect(self._export_subtitles)
        self._render_button = QPushButton("Renderizar MP4")
        self._render_button.setObjectName("primary")
        self._render_button.clicked.connect(self._render_video)
        heading.addWidget(self._save_status)
        heading.addWidget(self._export_ass_button)
        heading.addWidget(self._render_button)
        heading.addWidget(self._save_button)
        layout.addLayout(heading)

        content = QHBoxLayout()
        preview_panel = QWidget(root)
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        self._preview = VideoPreview(
            str(self._editor.project.source_video),
            self._editor,
            metadata,
            preview_panel,
            frame_renderer=self._render_preview_frame,
        )
        self._preview.captionSelected.connect(self._select_caption)
        self._preview.captionMoved.connect(self._on_caption_moved)
        self._preview.captionDeleted.connect(self._delete_caption_from_canvas)
        self._preview.captionEdited.connect(self._on_project_changed)
        self._preview.frameError.connect(self._on_preview_error)
        preview_layout.addWidget(self._preview, 1)
        timeline_header = QHBoxLayout()
        timeline_header.addWidget(QLabel("TIMELINE"))
        timeline_header.addStretch(1)
        zoom_out = QPushButton("−")
        zoom_in = QPushButton("+")
        fit_timeline = QPushButton("Ajustar")
        self._timeline_scroll = QSlider(Qt.Orientation.Horizontal)
        self._timeline_scroll.setRange(0, 0)
        timeline_header.addWidget(zoom_out)
        timeline_header.addWidget(zoom_in)
        timeline_header.addWidget(self._timeline_scroll, 1)
        timeline_header.addWidget(fit_timeline)
        preview_layout.addLayout(timeline_header)
        self._timeline = TimelineEditor(
            editor, max(metadata.duration, 0.1), preview_panel
        )
        self._timeline.captionSelected.connect(self._select_caption)
        self._timeline.seekRequested.connect(self._preview.seek)
        self._timeline.timingChanged.connect(self._update_timeline_timing)
        self._timeline.viewChanged.connect(self._on_timeline_view_changed)
        self._timeline_scroll.valueChanged.connect(
            lambda value: self._timeline.set_view_start(value / 1000)
        )
        self._preview.timeChanged.connect(self._timeline.set_current_time)
        zoom_out.clicked.connect(lambda: self._timeline.zoom(0.8))
        zoom_in.clicked.connect(lambda: self._timeline.zoom(1.25))
        fit_timeline.clicked.connect(self._timeline.fit)
        preview_layout.addWidget(self._timeline)
        content.addWidget(preview_panel, 1)
        self._tabs = QTabWidget(root)
        self._tabs.setMinimumWidth(330)
        self._tabs.setMaximumWidth(440)
        self._tabs.addTab(self._text_tab(), "Texto")
        self._tabs.addTab(self._animation_tab(), "Animación")
        self._tabs.addTab(self._tracking_tab(), "Tracking")
        self._tabs.addTab(self._export_tab(), "Exportar")
        content.addWidget(self._tabs)
        layout.addLayout(content, 1)
        self.setCentralWidget(root)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(650)
        self._save_timer.timeout.connect(self._save_project)
        self._refresh_controls()
        self.installEventFilter(self)
        for child in self.findChildren(QWidget):
            child.installEventFilter(self)
        self._save_project()

    def _text_tab(self) -> QWidget:
        widget = QWidget()
        outer = QVBoxLayout(widget)
        self._text_subtabs = QTabWidget(widget)
        basic = QWidget()
        layout = QVBoxLayout(basic)
        layout.addWidget(QLabel("Captions"))
        self._caption_list = QListWidget()
        self._caption_list.currentRowChanged.connect(self._select_caption_row)
        layout.addWidget(self._caption_list, 1)
        self._layout_warning = QLabel()
        self._layout_warning.setObjectName("layoutWarning")
        self._layout_warning.setWordWrap(True)
        layout.addWidget(self._layout_warning)
        layout.addWidget(QLabel("Texto"))
        self._text = QPlainTextEdit()
        self._text.setMaximumHeight(80)
        self._text.setPlaceholderText("Default text")
        self._text.textChanged.connect(self._update_text)
        layout.addWidget(self._text)
        layout.addWidget(QLabel("Palabras · selecciona para reordenar o dividir"))
        self._word_list = QListWidget()
        self._word_list.currentRowChanged.connect(self._select_word)
        layout.addWidget(self._word_list, 1)
        actions = QGridLayout()
        self._split = QPushButton("Dividir")
        self._merge = QPushButton("Unir con siguiente")
        self._word_up = QPushButton("Palabra ↑")
        self._word_down = QPushButton("Palabra ↓")
        self._word_previous = QPushButton("Mover a anterior")
        self._word_next = QPushButton("Mover a siguiente")
        self._delete = QPushButton("Eliminar palabra")
        self._split.clicked.connect(self._split_caption)
        self._merge.clicked.connect(self._merge_caption)
        self._word_up.clicked.connect(lambda: self._move_word(-1))
        self._word_down.clicked.connect(lambda: self._move_word(1))
        self._word_previous.clicked.connect(
            lambda: self._move_word_to_adjacent_caption(-1)
        )
        self._word_next.clicked.connect(lambda: self._move_word_to_adjacent_caption(1))
        self._delete.clicked.connect(self._delete_word)
        for index, button in enumerate(
            (
                self._split,
                self._merge,
                self._word_up,
                self._word_down,
                self._word_previous,
                self._word_next,
                self._delete,
            )
        ):
            actions.addWidget(button, index // 2, index % 2)
        layout.addLayout(actions)
        layout.addWidget(self._style_tab())
        layout.addWidget(self._layout_tab())
        basic_scroll = QScrollArea()
        basic_scroll.setWidgetResizable(True)
        basic_scroll.setWidget(basic)
        self._text_subtabs.addTab(basic_scroll, "Básico")
        self._text_subtabs.addTab(self._bubble_tab(), "Burbuja")
        self._text_subtabs.addTab(self._effects_tab(), "Efectos")
        outer.addWidget(self._text_subtabs)
        return widget

    def _animation_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        self._animation_style = self._combo()
        for style in StyleRegistry().all():
            self._animation_style.addItem(style.display_name, style.style_id)
        self._animation_style.currentIndexChanged.connect(self._update_animation_style)
        layout.addWidget(QLabel("Animación"))
        layout.addWidget(self._animation_style)
        self._animation_duration = QSpinBox()
        self._animation_duration.setRange(0, 2000)
        self._animation_duration.setSuffix(" ms")
        self._animation_duration.valueChanged.connect(self._update_animation_duration)
        layout.addWidget(QLabel("Duración de animación"))
        layout.addWidget(self._animation_duration)
        self._fade_enabled = QCheckBox("Fade")
        self._highlight_enabled = QCheckBox("Resaltado de palabra activa")
        self._fade_enabled.toggled.connect(self._update_fade_enabled)
        self._highlight_enabled.toggled.connect(self._update_highlight_enabled)
        layout.addWidget(self._fade_enabled)
        layout.addWidget(self._highlight_enabled)
        layout.addStretch(1)
        return widget

    def _layout_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        grid = QGridLayout()
        self._x = self._double_spin(0, 1, 0.01, 0.5)
        self._y = self._double_spin(0, 1, 0.01, 0.82)
        self._x.setRange(0, max(1, self._metadata.width))
        self._y.setRange(0, max(1, self._metadata.height))
        self._x.setDecimals(0)
        self._y.setDecimals(0)
        self._scale = self._double_spin(0.25, 4, 0.05, 1)
        self._spacing = self._double_spin(-20, 100, 0.5, 0)
        self._rotation = self._double_spin(-180, 180, 1, 0)
        self._start = self._double_spin(0, 100000, 0.01, 0)
        self._end = self._double_spin(0.01, 100000, 0.01, 1)
        rows = (
            ("X (px de video)", self._x),
            ("Y (px de video)", self._y),
            ("Escala", self._scale),
            ("Espaciado", self._spacing),
            ("Rotación", self._rotation),
            ("Inicio (s)", self._start),
            ("Final (s)", self._end),
        )
        for row, (label, control) in enumerate(rows):
            grid.addWidget(QLabel(label), row, 0)
            grid.addWidget(control, row, 1)
        layout.addLayout(grid)
        self._x.valueChanged.connect(self._update_position)
        self._y.valueChanged.connect(self._update_position)
        self._scale.valueChanged.connect(self._update_scale)
        self._spacing.valueChanged.connect(self._update_spacing)
        self._rotation.valueChanged.connect(self._update_rotation)
        self._start.valueChanged.connect(self._update_timing)
        self._end.valueChanged.connect(self._update_timing)
        layout.addWidget(QLabel("Arrastra el caption en el video para ajustar su posición."))
        layout.addStretch(1)
        return widget

    def _style_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        self._style = self._combo()
        for style in StyleRegistry().all():
            self._style.addItem(style.display_name, style.style_id)
        self._style.currentIndexChanged.connect(self._update_style)
        self._font = QFontComboBox()
        self._font.currentFontChanged.connect(self._update_font)
        self._font_size = QSpinBox()
        self._font_size.setRange(8, 240)
        self._font_size.valueChanged.connect(self._update_font_size)
        self._font_size_slider = QSlider(Qt.Orientation.Horizontal)
        self._font_size_slider.setRange(8, 240)
        self._font_size_slider.valueChanged.connect(self._font_size.setValue)
        self._font_size.valueChanged.connect(self._font_size_slider.setValue)
        self._bold = QCheckBox("Negrita")
        self._italic = QCheckBox("Cursiva")
        self._underline = QCheckBox("Subrayado")
        self._bold.toggled.connect(lambda value: self._update_style_config(bold=value))
        self._italic.toggled.connect(lambda value: self._update_style_config(italic=value))
        self._underline.toggled.connect(
            lambda value: self._update_style_config(underline=value)
        )
        self._case = self._combo()
        for label, value in (
            ("Normal", "none"),
            ("UPPER", "upper"),
            ("lower", "lower"),
            ("Capitalize", "capitalize"),
        ):
            self._case.addItem(label, value)
        self._case.currentIndexChanged.connect(
            lambda _index: self._update_style_config(case_mode=self._case.currentData())
        )
        self._line_spacing = self._double_spin(0, 100, 1, 0)
        self._line_spacing.valueChanged.connect(
            lambda value: self._update_style_config(line_spacing=value)
        )
        grid = QGridLayout()
        grid.addWidget(QLabel("Preset"), 0, 0)
        grid.addWidget(self._style, 0, 1)
        grid.addWidget(QLabel("Fuente"), 1, 0)
        grid.addWidget(self._font, 1, 1)
        grid.addWidget(QLabel("Tamaño"), 2, 0)
        size_controls = QHBoxLayout()
        size_controls.addWidget(self._font_size_slider, 1)
        size_controls.addWidget(self._font_size)
        grid.addLayout(size_controls, 2, 1)
        grid.addWidget(QLabel("Caso"), 3, 0)
        grid.addWidget(self._case, 3, 1)
        grid.addWidget(QLabel("Interlineado"), 4, 0)
        grid.addWidget(self._line_spacing, 4, 1)
        layout.addLayout(grid)
        font_toggles = QHBoxLayout()
        font_toggles.addWidget(self._bold)
        font_toggles.addWidget(self._italic)
        font_toggles.addWidget(self._underline)
        layout.addLayout(font_toggles)
        self._color_buttons: dict[str, QPushButton] = {}
        for key, label in (
            ("primary_color", "Texto"),
            ("highlight_color", "Resaltado"),
            ("outline_color", "Contorno"),
            ("shadow_color", "Sombra"),
        ):
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, color_key=key: self._choose_color(color_key))
            self._color_buttons[key] = button
            layout.addWidget(button)
        self._uppercase = QCheckBox("Usar mayúsculas")
        self._uppercase.toggled.connect(self._update_uppercase)
        layout.addWidget(self._uppercase)
        save_preset = QPushButton("Guardar como preset…")
        save_preset.clicked.connect(self._save_preset)
        layout.addWidget(save_preset)
        recalculate_layout = QPushButton("Recalcular layout")
        recalculate_layout.clicked.connect(self._relayout)
        layout.addWidget(recalculate_layout)
        rebuild = QPushButton("Reconstruir desde la transcripción…")
        rebuild.clicked.connect(self._rebuild_from_transcript)
        layout.addWidget(rebuild)
        layout.addStretch(1)
        return widget

    def _bubble_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        self._bubble_enabled = QCheckBox("Activar fondo")
        self._bubble_enabled.toggled.connect(
            lambda value: self._update_style_config(bubble_enabled=value)
        )
        layout.addWidget(self._bubble_enabled)
        bubble_color = QPushButton("Color de fondo")
        bubble_color.clicked.connect(lambda: self._choose_color("background_color"))
        layout.addWidget(bubble_color)
        self._bubble_opacity = QSpinBox()
        self._bubble_opacity.setRange(0, 100)
        self._bubble_opacity.setSuffix(" %")
        self._bubble_opacity.valueChanged.connect(
            lambda value: self._update_style_config(bubble_opacity=value)
        )
        self._bubble_padding = self._double_spin(0, 100, 1, 8)
        self._bubble_padding.valueChanged.connect(
            lambda value: self._update_style_config(bubble_padding=value)
        )
        for label, control in (
            ("Opacidad", self._bubble_opacity),
            ("Relleno", self._bubble_padding),
        ):
            layout.addWidget(QLabel(label))
            layout.addWidget(control)
        layout.addWidget(QLabel("libass representa la burbuja con esquinas cuadradas."))
        layout.addStretch(1)
        return widget

    def _effects_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        self._outline_width = self._double_spin(0, 30, 0.5, 4)
        self._shadow_depth = self._double_spin(0, 30, 0.5, 1.5)
        self._blur = self._double_spin(0, 20, 0.1, 0)
        self._glow_enabled = QCheckBox("Activar resplandor")
        self._glow_radius = self._double_spin(0, 20, 0.1, 0)
        self._outline_width.valueChanged.connect(
            lambda value: self._update_style_config(outline_width=value)
        )
        self._shadow_depth.valueChanged.connect(
            lambda value: self._update_style_config(shadow_depth=value)
        )
        self._blur.valueChanged.connect(
            lambda value: self._update_style_config(blur=value)
        )
        self._glow_enabled.toggled.connect(
            lambda value: self._update_style_config(glow_enabled=value)
        )
        self._glow_radius.valueChanged.connect(
            lambda value: self._update_style_config(glow_radius=value)
        )
        for label, control in (
            ("Contorno", self._outline_width),
            ("Sombra", self._shadow_depth),
            ("Blur", self._blur),
        ):
            layout.addWidget(QLabel(label))
            layout.addWidget(control)
        layout.addWidget(self._glow_enabled)
        layout.addWidget(QLabel("Radio del resplandor"))
        layout.addWidget(self._glow_radius)
        layout.addWidget(self._color_buttons["outline_color"])
        layout.addWidget(self._color_buttons["shadow_color"])
        layout.addStretch(1)
        return widget

    def _export_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        export = QPushButton("Exportar ASS/SRT")
        export.clicked.connect(self._export_subtitles)
        render = QPushButton("Renderizar MP4")
        render.clicked.connect(self._render_video)
        verify = QPushButton("Verificar frame exacto")
        verify.clicked.connect(self._verify_exact_frame)
        layout.addWidget(QLabel("Exporta el estilo actual sin volver a transcribir."))
        layout.addWidget(export)
        layout.addWidget(render)
        layout.addWidget(verify)
        layout.addStretch(1)
        return widget

    @staticmethod
    def _tracking_tab() -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.addWidget(QLabel("El seguimiento de objetos se añadirá en una fase posterior."))
        layout.addStretch(1)
        return widget

    @staticmethod
    def _combo():
        from PySide6.QtWidgets import QComboBox

        return QComboBox()

    @staticmethod
    def _double_spin(minimum: float, maximum: float, step: float, value: float):
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setSingleStep(step)
        spin.setDecimals(3)
        spin.setValue(value)
        return spin

    def _refresh_controls(self) -> None:
        self._updating_controls = True
        project = self._editor.project
        self._caption_list.clear()
        selected_row = -1
        for row, caption in enumerate(project.captions):
            marker = "⚠ " if caption.layout_warning else ""
            self._caption_list.addItem(
                f"{marker}{caption.start:0.2f}–{caption.end:0.2f}  {caption.text}"
            )
            if caption.caption_id == self._selected_caption_id:
                selected_row = row
        if selected_row >= 0:
            self._caption_list.setCurrentRow(selected_row)
        caption = self._selected_caption()
        self._word_list.clear()
        if caption:
            self._layout_warning.setText(
                f"⚠ {caption.layout_warning}" if caption.layout_warning else ""
            )
            for word in caption.words:
                self._word_list.addItem(
                    f"{word.text}    {word.start:0.2f} → {word.end:0.2f}"
                )
            self._word_list.setCurrentRow(
                min(self._selected_word_index, len(caption.words) - 1)
            )
            if self._text.toPlainText() != caption.text:
                self._text.setPlainText(caption.text)
            self._x.setValue(caption.x * self._metadata.width)
            self._y.setValue(caption.y * self._metadata.height)
            self._scale.setValue(caption.scale)
            self._spacing.setValue(caption.word_spacing)
            self._rotation.setValue(caption.rotation_deg)
            self._start.setValue(caption.start)
            self._end.setValue(caption.end)
        else:
            self._text.setPlainText("Default text")
            self._layout_warning.clear()
        style = project.style_config
        available_fonts = {
            family.casefold() for family in QFontDatabase.families()
        }
        self._missing_font = style.font_family.casefold() not in available_fonts
        self._font.setToolTip(
            "Esta fuente no está instalada en el equipo."
            if self._missing_font
            else ""
        )
        style_index = self._style.findData(project.style_id)
        if style_index >= 0:
            self._style.setCurrentIndex(style_index)
            self._animation_style.setCurrentIndex(style_index)
        self._font.setCurrentFont(QFont(style.font_family))
        self._font_size.setValue(style.font_size)
        self._font_size_slider.setValue(style.font_size)
        self._bold.setChecked(style.bold)
        self._italic.setChecked(style.italic)
        self._underline.setChecked(style.underline)
        case_index = self._case.findData(style.case_mode)
        self._case.setCurrentIndex(max(0, case_index))
        self._line_spacing.setValue(style.line_spacing)
        self._bubble_enabled.setChecked(style.bubble_enabled)
        self._bubble_opacity.setValue(style.bubble_opacity)
        self._bubble_padding.setValue(style.bubble_padding)
        self._outline_width.setValue(style.outline_width)
        self._shadow_depth.setValue(style.shadow_depth)
        self._blur.setValue(style.blur)
        self._glow_enabled.setChecked(style.glow_enabled)
        self._glow_radius.setValue(style.glow_radius)
        self._uppercase.setChecked(style.uppercase)
        self._animation_duration.setValue(style.animation_duration_ms)
        behavior = StyleRegistry().get(project.style_id).behavior
        self._fade_enabled.setChecked(
            style.fade_enabled if style.fade_enabled is not None else behavior == "karaoke"
        )
        self._highlight_enabled.setChecked(
            style.highlight_enabled
            if style.highlight_enabled is not None
            else behavior != "one_word"
        )
        for key, button in self._color_buttons.items():
            color = getattr(style, key)
            button.setStyleSheet(f"background: {color};")
        self._preview.set_selected_caption(self._selected_caption_id)
        self._preview.set_selected_word(self._selected_word_index)
        self._timeline.set_selected_caption(self._selected_caption_id)
        self._timeline.refresh()
        self._updating_controls = False

    def _selected_caption(self):
        return next(
            (
                caption
                for caption in self._editor.project.captions
                if caption.caption_id == self._selected_caption_id
            ),
            None,
        )

    @Slot(int)
    def _select_caption_row(self, row: int) -> None:
        if self._updating_controls:
            return
        captions = self._editor.project.captions
        if 0 <= row < len(captions):
            self._select_caption(captions[row].caption_id)

    @Slot(str)
    def _select_caption(self, caption_id: str) -> None:
        if self._updating_controls:
            return
        caption = next(
            (item for item in self._editor.project.captions if item.caption_id == caption_id),
            None,
        )
        if caption is None:
            return
        self._selected_caption_id = caption_id
        self._selected_word_index = 0
        self._preview.set_selected_caption(caption_id)
        self._preview.seek(caption.start)
        self._refresh_controls()

    @Slot(int)
    def _select_word(self, row: int) -> None:
        if self._updating_controls:
            return
        self._selected_word_index = max(0, row)
        self._preview.set_selected_word(self._selected_word_index)

    @Slot()
    def _update_text(self) -> None:
        if self._updating_controls or not self._selected_caption_id:
            return
        try:
            self._editor.update_text(self._selected_caption_id, self._text.toPlainText())
        except ValueError:
            return
        self._on_project_changed()

    @Slot()
    def _split_caption(self) -> None:
        caption = self._selected_caption()
        if not caption:
            return
        split_at = self._selected_word_index
        if split_at <= 0 or split_at >= len(caption.words):
            split_at = len(caption.words) // 2
        try:
            _left_id, right_id = self._editor.split_caption(caption.caption_id, split_at)
        except ValueError as exc:
            QMessageBox.information(self, "No se pudo dividir", str(exc))
            return
        self._selected_caption_id = right_id
        self._selected_word_index = 0
        self._on_project_changed()

    @Slot()
    def _merge_caption(self) -> None:
        if not self._selected_caption_id:
            return
        try:
            self._selected_caption_id = self._editor.merge_with_next(self._selected_caption_id)
        except ValueError as exc:
            QMessageBox.information(self, "No se pudo unir", str(exc))
            return
        self._on_project_changed()

    def _move_word(self, amount: int) -> None:
        caption = self._selected_caption()
        if caption is None:
            return
        target = self._selected_word_index + amount
        try:
            self._editor.reorder_word(caption.caption_id, self._selected_word_index, target)
        except ValueError:
            return
        self._selected_word_index = target
        self._on_project_changed()

    @Slot()
    def _delete_word(self) -> None:
        if not self._selected_caption_id:
            return
        try:
            self._editor.delete_word(
                self._selected_caption_id, self._selected_word_index
            )
        except ValueError:
            return
        captions = self._editor.project.captions
        if any(caption.caption_id == self._selected_caption_id for caption in captions):
            self._selected_word_index = max(0, self._selected_word_index - 1)
        else:
            self._selected_caption_id = captions[0].caption_id if captions else None
            self._selected_word_index = 0
        self._on_project_changed()

    def _move_word_to_adjacent_caption(self, direction: int) -> None:
        caption = self._selected_caption()
        if caption is None or not self._selected_caption_id:
            return
        captions = self._editor.project.captions
        index = next(
            i for i, item in enumerate(captions) if item.caption_id == caption.caption_id
        )
        target_index = index + direction
        if not 0 <= target_index < len(captions):
            return
        target = captions[target_index]
        try:
            self._editor.move_word(
                caption.caption_id,
                self._selected_word_index,
                target.caption_id,
                len(target.words),
            )
        except ValueError as exc:
            QMessageBox.information(self, "No se pudo mover", str(exc))
            return
        self._selected_caption_id = target.caption_id
        self._selected_word_index = len(target.words)
        self._on_project_changed()

    @Slot(float)
    def _update_position(self, _value: float) -> None:
        if not self._updating_controls and self._selected_caption_id:
            self._editor.move_caption(
                self._selected_caption_id,
                self._x.value() / self._metadata.width,
                self._y.value() / self._metadata.height,
            )
            self._on_project_changed()

    @Slot(float)
    def _update_scale(self, value: float) -> None:
        if not self._updating_controls and self._selected_caption_id:
            self._editor.resize_caption(self._selected_caption_id, value)
            self._on_project_changed()

    @Slot(float)
    def _update_spacing(self, value: float) -> None:
        if not self._updating_controls and self._selected_caption_id:
            self._editor.set_word_spacing(self._selected_caption_id, value)
            self._on_project_changed()

    @Slot(float)
    def _update_rotation(self, value: float) -> None:
        if not self._updating_controls and self._selected_caption_id:
            self._editor.rotate_caption(self._selected_caption_id, value)
            self._on_project_changed()

    @Slot(float)
    def _update_timing(self, _value: float) -> None:
        if self._updating_controls or not self._selected_caption_id:
            return
        try:
            self._editor.set_timing(
                self._selected_caption_id, self._start.value(), self._end.value()
            )
        except ValueError:
            self._refresh_controls()
            return
        self._on_project_changed()

    @Slot(str, float, float)
    def _update_timeline_timing(self, caption_id: str, start: float, end: float) -> None:
        try:
            self._editor.set_timing(caption_id, start, end)
        except ValueError:
            return
        self._selected_caption_id = caption_id
        self._on_project_changed()

    @Slot(float, float)
    def _on_timeline_view_changed(self, start: float, view_duration: float) -> None:
        maximum = max(0, round((self._metadata.duration - view_duration) * 1000))
        self._timeline_scroll.blockSignals(True)
        self._timeline_scroll.setRange(0, maximum)
        self._timeline_scroll.setValue(min(maximum, round(start * 1000)))
        self._timeline_scroll.setEnabled(maximum > 0)
        self._timeline_scroll.blockSignals(False)

    @Slot()
    def _update_style(self, *_args) -> None:
        if self._updating_controls:
            return
        self._update_style_config(style_id=self._style.currentData())

    @Slot()
    def _update_animation_style(self, *_args) -> None:
        if self._updating_controls:
            return
        style_id = self._animation_style.currentData()
        self._update_style_config(style_id=style_id)
        style_index = self._style.findData(style_id)
        if style_index >= 0:
            self._style.setCurrentIndex(style_index)

    @Slot()
    def _update_font(self, font) -> None:
        if not self._updating_controls:
            self._update_style_config(font_family=font.family())

    @Slot(int)
    def _update_font_size(self, value: int) -> None:
        if not self._updating_controls:
            self._update_style_config(font_size=value)

    @Slot(bool)
    def _update_uppercase(self, value: bool) -> None:
        if not self._updating_controls:
            self._update_style_config(uppercase=value)

    @Slot(int)
    def _update_animation_duration(self, value: int) -> None:
        if not self._updating_controls:
            self._update_style_config(animation_duration_ms=value)

    @Slot(bool)
    def _update_fade_enabled(self, value: bool) -> None:
        if not self._updating_controls:
            self._update_style_config(fade_enabled=value)

    @Slot(bool)
    def _update_highlight_enabled(self, value: bool) -> None:
        if not self._updating_controls:
            self._update_style_config(highlight_enabled=value)

    def _choose_color(self, key: str) -> None:
        current = QColor(getattr(self._editor.project.style_config, key))
        selected = QColorDialog.getColor(current, self, "Seleccionar color")
        if selected.isValid():
            self._update_style_config(**{key: selected.name().upper()})

    def _update_style_config(self, *, style_id: str | None = None, **changes) -> None:
        if self._updating_controls:
            return
        project = self._editor.project
        try:
            self._editor.set_style(
                style_id or project.style_id,
                replace(project.style_config, **changes),
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Configuración no válida", str(exc))
            self._refresh_controls()
            return
        self._on_project_changed()

    @Slot()
    def _relayout(self) -> None:
        try:
            self._editor.relayout()
        except (ValueError, RuntimeError) as exc:
            QMessageBox.warning(self, "No se pudo recalcular el layout", str(exc))
            return
        self._on_project_changed()

    @Slot()
    def _rebuild_from_transcript(self) -> None:
        answer = QMessageBox.question(
            self,
            "Reconstruir captions",
            "Se reemplazarán los captions actuales con una nueva segmentación "
            "de transcript.json y se perderán sus ediciones. ¿Continuar?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            transcript_path = self._project_path.with_name("transcript.json")
            transcript = (
                self._export_service.load_transcript(transcript_path)
                if transcript_path.is_file()
                else None
            )
            self._editor.rebuild_from_transcript(transcript)
        except (OSError, ValueError, RuntimeError) as exc:
            QMessageBox.warning(self, "No se pudo reconstruir", str(exc))
            return
        captions = self._editor.project.captions
        self._selected_caption_id = captions[0].caption_id if captions else None
        self._selected_word_index = 0
        self._on_project_changed()

    def _save_preset(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Guardar preset de estilo", "caption-style.json", "JSON (*.json)"
        )
        if not path:
            return
        payload = {
            "style_id": self._editor.project.style_id,
            "style_config": asdict(self._editor.project.style_config),
        }
        try:
            Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.exception("No se pudo guardar el preset")
            QMessageBox.critical(
                self, "Error al guardar", f"No se pudo guardar el preset:\n{exc}"
            )

    def _on_project_changed(self, *_args) -> None:
        self._timeline.refresh()
        self._preview.refresh_frame()
        self._refresh_controls()
        self._save_status.setText("Cambios sin guardar…")
        self._save_timer.start()

    @Slot(str)
    def _on_caption_moved(self, caption_id: str) -> None:
        self._selected_caption_id = caption_id
        caption = self._selected_caption()
        if caption is not None:
            self._updating_controls = True
            self._x.setValue(caption.x * self._metadata.width)
            self._y.setValue(caption.y * self._metadata.height)
            self._scale.setValue(caption.scale)
            self._rotation.setValue(caption.rotation_deg)
            self._updating_controls = False
        self._timeline.refresh()
        self._preview.refresh_frame()
        self._save_status.setText("Cambios sin guardar…")
        self._save_timer.start()

    @Slot(str)
    def _delete_caption_from_canvas(self, caption_id: str) -> None:
        self._editor.delete_caption(caption_id)
        captions = self._editor.project.captions
        self._selected_caption_id = captions[0].caption_id if captions else None
        self._selected_word_index = 0
        self._on_project_changed()

    @Slot(str)
    def _on_preview_error(self, message: str) -> None:
        self._save_status.setText("Vista previa ASS no disponible")
        self._save_status.setToolTip(message)

    @Slot()
    def _save_project(self) -> bool:
        try:
            self._editor.save(self._project_path)
        except OSError as exc:
            logger.exception("No se pudo guardar el proyecto editable")
            self._save_status.setText("Error al guardar")
            QMessageBox.critical(
                self, "Error al guardar", f"No se pudo guardar el proyecto:\n{exc}"
            )
            return False
        self._save_status.setText("Guardado")
        return True

    @Slot()
    def _export_subtitles(self) -> None:
        if not self._require_installed_font():
            return
        self._save_timer.stop()
        if not self._save_project():
            return
        try:
            result = self._export_service.export_editor_project(
                self._editor.project,
                self._metadata,
                self._project_path.parent,
                render_video=False,
            )
        except Exception as exc:
            logger.exception("No se pudieron exportar los subtítulos editados")
            QMessageBox.critical(
                self, "Error de exportación", f"No se pudieron exportar los subtítulos:\n{exc}"
            )
            return
        QMessageBox.information(
            self,
            "Exportación completada",
            f"ASS y SRT guardados en:\n{result.output_directory}",
        )

    @Slot()
    def _render_video(self) -> None:
        if not self._require_installed_font():
            return
        if self._render_thread is not None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Exportar video")
        form = QFormLayout(dialog)
        name = QLineEdit("video_captioned.mp4", dialog)
        directory = QLineEdit(str(self._project_path.parent), dialog)
        choose_directory = QPushButton("Elegir…", dialog)
        choose_directory.clicked.connect(
            lambda: directory.setText(
                QFileDialog.getExistingDirectory(
                    dialog, "Carpeta de exportación", directory.text()
                )
                or directory.text()
            )
        )
        directory_row = QHBoxLayout()
        directory_row.addWidget(directory, 1)
        directory_row.addWidget(choose_directory)
        quality = QComboBox(dialog)
        quality.addItem("Alta calidad", 18)
        quality.addItem("Equilibrada", 20)
        quality.addItem("Archivo pequeño", 25)
        form.addRow("Nombre del archivo", name)
        form.addRow("Carpeta", directory_row)
        form.addRow("Calidad", quality)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=dialog,
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        output_directory = Path(directory.text()).expanduser()
        output_name = Path(name.text()).name
        if not output_directory.is_dir():
            QMessageBox.warning(self, "Carpeta no válida", "Elige una carpeta de exportación existente.")
            return
        if not output_name or output_name in {".", ".."}:
            QMessageBox.warning(self, "Nombre no válido", "Escribe un nombre de archivo.")
            return
        self._save_timer.stop()
        if not self._save_project():
            return
        self._render_button.setEnabled(False)
        self._save_status.setText("Renderizando…")
        self._render_thread = QThread(self)
        self._render_worker = EditorRenderWorker(
            self._export_service,
            self._editor,
            self._metadata,
            output_directory,
            output_name,
            int(quality.currentData()),
        )
        self._render_worker.moveToThread(self._render_thread)
        self._render_thread.started.connect(self._render_worker.run)
        self._render_worker.progress.connect(self._on_render_progress)
        self._render_worker.succeeded.connect(self._on_render_success)
        self._render_worker.failed.connect(self._on_render_failure)
        self._render_worker.finished.connect(self._render_thread.quit)
        self._render_worker.finished.connect(self._render_worker.deleteLater)
        self._render_thread.finished.connect(self._render_thread.deleteLater)
        self._render_thread.finished.connect(self._on_render_finished)
        self._render_thread.start()

    @Slot()
    def _verify_exact_frame(self) -> None:
        if not self._require_installed_font():
            return
        try:
            image_data = self._render_preview_frame(self._preview.position)
        except Exception as exc:
            logger.exception("No se pudo verificar el fotograma exacto")
            QMessageBox.critical(
                self,
                "Verificación fallida",
                f"No se pudo generar el fotograma con FFmpeg/libass:\n{exc}",
            )
            return
        pixmap = QPixmap()
        if not pixmap.loadFromData(image_data):
            QMessageBox.critical(
                self, "Verificación fallida", "FFmpeg devolvió una imagen no válida."
            )
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Frame exacto · {self._preview.position:.3f} s")
        layout = QVBoxLayout(dialog)
        image = QLabel(dialog)
        image.setPixmap(
            pixmap.scaled(
                960,
                600,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        layout.addWidget(image)
        layout.addWidget(QLabel(
            "Render FFmpeg/libass del mismo ASS usado en la exportación. "
            "Compáralo con el preview del editor."
        ))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=dialog)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def _require_installed_font(self) -> bool:
        if not self._missing_font:
            return True
        QMessageBox.warning(
            self,
            "Fuente no instalada",
            f"La fuente «{self._editor.project.style_config.font_family}» no está "
            "instalada. Elige una fuente disponible antes de exportar.",
        )
        return False

    def _render_preview_frame(self, seconds: float) -> bytes:
        if self._missing_font:
            raise ValueError(
                f"La fuente «{self._editor.project.style_config.font_family}» no está "
                "instalada en este equipo."
            )
        return self._export_service.render_editor_preview_frame(
            self._editor.project, self._metadata, seconds
        )

    @Slot(str, int)
    def _on_render_progress(self, message: str, _percent: int) -> None:
        self._save_status.setText(message)

    @Slot(object)
    def _on_render_success(self, result: PipelineResult) -> None:
        self._save_status.setText(f"Render listo · {result.video.name}")
        QMessageBox.information(
            self, "Render completado", f"Video guardado en:\n{result.video}"
        )

    @Slot(str)
    def _on_render_failure(self, message: str) -> None:
        self._save_status.setText("Falló el render")
        QMessageBox.critical(self, "Error de render", message)

    @Slot()
    def _on_render_finished(self) -> None:
        self._render_worker = None
        self._render_thread = None
        self._render_button.setEnabled(True)

    @Slot()
    def _undo(self) -> None:
        if self._editor.undo():
            self._selected_caption_id = (
                self._selected_caption_id
                if any(
                    caption.caption_id == self._selected_caption_id
                    for caption in self._editor.project.captions
                )
                else self._editor.project.captions[0].caption_id
                if self._editor.project.captions
                else None
            )
            self._on_project_changed()

    @Slot()
    def _redo(self) -> None:
        if self._editor.redo():
            self._on_project_changed()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._preview.is_rendering_frame:
            QMessageBox.information(
                self,
                "Vista previa en curso",
                "Espera a que termine la actualización de vista previa antes de cerrar.",
            )
            event.ignore()
            return
        if self._render_thread and self._render_thread.isRunning():
            QMessageBox.information(
                self,
                "Render en curso",
                "Espera a que termine el render antes de cerrar el editor.",
            )
            event.ignore()
            return
        self._save_timer.stop()
        if not self._save_project():
            event.ignore()
            return
        super().closeEvent(event)

    def eventFilter(self, watched, event) -> bool:
        if event.type() != QEvent.Type.KeyPress or not isinstance(event, QKeyEvent):
            return super().eventFilter(watched, event)
        focus = self.focusWidget()
        if isinstance(
            focus,
            (
                QAbstractButton,
                QComboBox,
                QDoubleSpinBox,
                QPlainTextEdit,
                QSpinBox,
            ),
        ):
            return super().eventFilter(watched, event)

        key = event.key()
        modifiers = event.modifiers()
        if key in {
            Qt.Key.Key_Left,
            Qt.Key.Key_Right,
            Qt.Key.Key_Up,
            Qt.Key.Key_Down,
        } and self._preview.selected_caption_id:
            step = 10 if modifiers & Qt.KeyboardModifier.ShiftModifier else 1
            dx = (
                -step if key == Qt.Key.Key_Left
                else step if key == Qt.Key.Key_Right
                else 0
            )
            dy = (
                -step if key == Qt.Key.Key_Up
                else step if key == Qt.Key.Key_Down
                else 0
            )
            self._preview.move_selected_caption(dx, dy)
            return True
        command = bool(
            modifiers
            & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier)
        )
        if command and key == Qt.Key.Key_Z:
            self._redo() if modifiers & Qt.KeyboardModifier.ShiftModifier else self._undo()
        elif command and key == Qt.Key.Key_Y:
            self._redo()
        elif key == Qt.Key.Key_S:
            self._split_caption()
        elif key == Qt.Key.Key_M:
            self._merge_caption()
        elif key == Qt.Key.Key_Delete:
            self._delete_word()
        elif key == Qt.Key.Key_Left:
            self._preview.seek(max(0, self._preview.position - 0.04))
        elif key == Qt.Key.Key_Right:
            self._preview.seek(min(self._preview.duration, self._preview.position + 0.04))
        elif key == Qt.Key.Key_Space:
            self._preview.toggle_playback()
        else:
            return super().eventFilter(watched, event)
        return True
