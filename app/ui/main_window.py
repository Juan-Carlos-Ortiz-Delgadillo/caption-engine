"""Interfaz principal adaptable y arranque asíncrono del flujo de generación."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from html import escape
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Qt, QThread, Signal, Slot
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QFontMetrics,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFontComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.core.exceptions import CaptionStudioError
from app.core.models import CaptionStyleConfig, TranscriptionOptions, VideoMetadata
from app.editor.service import CaptionEditorService
from app.services.environment import EnvironmentReport, EnvironmentService
from app.services.pipeline import CaptionApplicationService
from app.styles.registry import StyleRegistry
from app.ui.caption_editor_window import CaptionEditorWindow
from app.ui.theme import STYLESHEET

logger = logging.getLogger(__name__)
_VIDEO_EXTENSIONS = {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".webm"}


class PipelineWorker(QObject):
    progress = Signal(str, int)
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, operation: Callable, args: tuple) -> None:
        super().__init__()
        self._operation = operation
        self._args = args

    @Slot()
    def run(self) -> None:
        try:
            result = self._operation(*self._args, progress=self.progress.emit)
            self.succeeded.emit(result)
        except Exception as exc:
            logger.exception("Falló el flujo de generación")
            message = (
                str(exc)
                if isinstance(exc, CaptionStudioError)
                else "No se pudo completar la generación. Consulta el registro de la aplicación."
            )
            self.failed.emit(message)
        finally:
            self.finished.emit()


class MainWindow(QMainWindow):
    def __init__(
        self,
        service: CaptionApplicationService,
        environment: EnvironmentService,
    ) -> None:
        super().__init__()
        self._service = service
        self._environment = environment
        self._report: EnvironmentReport | None = None
        self._video: Path | None = None
        self._transcript = None
        self._metadata: VideoMetadata | None = None
        self._workspace: Path | None = None
        self._reusable_transcript: Path | None = None
        self._pending_operation = ""
        self._current_step = 0
        self._thread: QThread | None = None
        self._worker: PipelineWorker | None = None
        self._preview_window: CaptionEditorWindow | None = None
        self._settings = QSettings("Animated Caption Studio", "Animated Caption Studio")
        self.setWindowTitle("Animated Caption Studio")
        self.setMinimumSize(820, 620)
        self.resize(920, 760)
        self.setAcceptDrops(True)
        self.setStyleSheet(STYLESHEET)
        self._build_ui()
        self._restore_preferences()
        self._connect_preferences()
        self._refresh_environment()

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("applicationRoot")
        outer = QVBoxLayout(root)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(14)

        header = QHBoxLayout()
        title = QLabel("Animated Caption Studio")
        title.setObjectName("title")
        header.addWidget(title)
        open_project = QPushButton("Abrir proyecto…")
        open_project.clicked.connect(self._open_project)
        header.addWidget(open_project)
        recent_button = QPushButton("Proyectos recientes")
        recent_button.clicked.connect(self._show_recent_projects)
        header.addWidget(recent_button)
        header.addStretch(1)
        self._environment_badge = QLabel("Verificando entorno…")
        self._environment_badge.setObjectName("environmentBadge")
        header.addWidget(self._environment_badge)
        outer.addLayout(header)

        self._step_heading = QLabel()
        self._step_heading.setObjectName("section")
        outer.addWidget(self._step_heading)
        self._pages = QStackedWidget()
        self._pages.setObjectName("workflowPages")

        video_page = QWidget()
        video_layout = QVBoxLayout(video_page)
        video_layout.addWidget(self._card("VIDEO", self._video_card()))
        video_layout.addWidget(self._card("ENTORNO DE EJECUCIÓN", self._environment_card()))
        video_layout.addStretch(1)
        self._pages.addWidget(self._scroll_page(video_page))

        transcription_page = QWidget()
        transcription_layout = QVBoxLayout(transcription_page)
        transcription_layout.addWidget(
            self._card("OPCIONES DE TRANSCRIPCIÓN", self._transcription_card())
        )
        self._transcript_status = QLabel("Todavía no hay una transcripción para este video.")
        self._transcript_status.setObjectName("hint")
        self._transcript_status.setWordWrap(True)
        transcription_layout.addWidget(self._transcript_status)
        transcription_layout.addStretch(1)
        self._pages.addWidget(self._scroll_page(transcription_page))

        style_page = QWidget()
        style_layout = QVBoxLayout(style_page)
        style_layout.addWidget(self._card("ESTILO", self._style_card()))
        self._style_preview = QLabel(
            "La vista previa usará palabras reales del transcript."
        )
        self._style_preview.setObjectName("stylePreview")
        self._style_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._style_preview.setWordWrap(True)
        self._style_preview.setMinimumHeight(120)
        self._style_preview.setTextFormat(Qt.TextFormat.RichText)
        style_layout.addWidget(self._card("VISTA PREVIA", self._style_preview))
        style_layout.addStretch(1)
        self._pages.addWidget(self._scroll_page(style_page))

        generation_page = QWidget()
        generation_layout = QVBoxLayout(generation_page)
        self._generation_summary = QLabel(
            "Se crearán los captions y su layout. No se exportará ASS/SRT ni se renderizará video."
        )
        self._generation_summary.setObjectName("hint")
        self._generation_summary.setWordWrap(True)
        generation_layout.addWidget(self._card("GENERACIÓN", self._generation_summary))
        generation_layout.addStretch(1)
        self._pages.addWidget(self._scroll_page(generation_page))
        outer.addWidget(self._pages, 1)

        self._status = QLabel("Selecciona o arrastra un video para comenzar.")
        self._status.setObjectName("status")
        self._status.setWordWrap(True)
        outer.addWidget(self._status)
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        outer.addWidget(self._progress)
        self._back = QPushButton("Anterior")
        self._back.clicked.connect(self._previous_step)
        self._next = QPushButton("Siguiente")
        self._next.setObjectName("primary")
        self._next.setMinimumHeight(46)
        self._next.clicked.connect(self._next_step)
        self._generate = QPushButton("Crear proyecto y abrir editor")
        self._generate.setObjectName("primary")
        self._generate.setMinimumHeight(46)
        self._generate.clicked.connect(self._start_layout)
        self._close_button = QPushButton("Cerrar aplicación")
        self._close_button.setObjectName("closeApplication")
        self._close_button.clicked.connect(self.close)
        actions = QHBoxLayout()
        actions.setSpacing(10)
        actions.addWidget(self._back)
        actions.addWidget(self._next, 1)
        actions.addWidget(self._generate, 1)
        actions.addWidget(self._close_button)
        outer.addLayout(actions)
        self.setCentralWidget(root)
        self._show_step(0)

    @staticmethod
    def _scroll_page(page: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setObjectName("contentScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(page)
        return scroll

    @staticmethod
    def _card(title: str, content: QWidget) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 15, 18, 18)
        layout.setSpacing(13)
        label = QLabel(title)
        label.setObjectName("section")
        layout.addWidget(label)
        layout.addWidget(content)
        return card

    def _video_card(self) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        self._video_label = QLabel("Arrastra un video aquí o selecciónalo")
        self._video_label.setObjectName("videoName")
        self._video_label.setMinimumWidth(0)
        self._video_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._video_label.setToolTip("Se mostrará aquí la ruta completa del video.")
        select_button = QPushButton("Seleccionar video…")
        select_button.clicked.connect(self._select_video)
        layout.addWidget(self._video_label, 1)
        layout.addWidget(select_button)
        return widget

    def _transcription_card(self) -> QWidget:
        widget = QWidget()
        grid = QGridLayout(widget)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(11)
        grid.setColumnMinimumWidth(0, 92)
        grid.setColumnMinimumWidth(2, 92)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)

        self._language = self._combo()
        for label, code in (
            ("Detectar automáticamente", ""),
            ("Español", "es"),
            ("English", "en"),
            ("Français", "fr"),
            ("Português", "pt"),
        ):
            self._language.addItem(label, code)
        self._model = self._combo()
        self._model.addItems(["tiny", "base", "small", "medium", "large-v3"])
        self._model.setCurrentText("small")
        self._device = self._combo()
        self._device.addItem("Automático", "auto")
        self._device.addItem("CPU", "cpu")
        self._compute_type = self._combo()
        self._compute_type.addItems(["int8", "default"])
        self._compute_type.setCurrentText("int8")

        for row, (left_label, left_widget, right_label, right_widget) in enumerate(
            (
                ("Idioma", self._language, "Modelo Whisper", self._model),
                ("Dispositivo", self._device, "Tipo de cómputo", self._compute_type),
            )
        ):
            grid.addWidget(self._field_label(left_label), row, 0)
            grid.addWidget(left_widget, row, 1)
            grid.addWidget(self._field_label(right_label), row, 2)
            grid.addWidget(right_widget, row, 3)
        note = QLabel(
            "El modelo se descarga la primera vez que lo uses. «Automático» selecciona "
            "un backend soportado por faster-whisper."
        )
        note.setObjectName("hint")
        note.setWordWrap(True)
        grid.addWidget(note, 2, 0, 1, 4)
        return widget

    def _style_card(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(11)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        grid.setColumnMinimumWidth(0, 92)
        grid.setColumnMinimumWidth(2, 92)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)

        self._style = self._combo()
        for style in StyleRegistry().all():
            self._style.addItem(style.display_name, style.style_id)
        self._font = QFontComboBox()
        self._font.setCurrentFont(QFont("Arial"))
        self._font.setMinimumWidth(190)
        self._font.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._font_size = QSpinBox()
        self._font_size.setRange(8, 240)
        self._font_size.setValue(64)
        self._font_size.setSuffix(" px")
        self._font_size.setMinimumWidth(112)
        self._position = self._combo()
        for key, label in (("top", "Arriba"), ("center", "Centro"), ("bottom", "Abajo")):
            self._position.addItem(label, key)
        for row, (label_a, field_a, label_b, field_b) in enumerate(
            (
                ("Preset", self._style, "Posición", self._position),
                ("Fuente", self._font, "Tamaño", self._font_size),
            )
        ):
            grid.addWidget(self._field_label(label_a), row, 0)
            grid.addWidget(field_a, row, 1)
            grid.addWidget(self._field_label(label_b), row, 2)
            grid.addWidget(field_b, row, 3)
        layout.addLayout(grid)

        self._colors: dict[str, str] = {
            "primary_color": "#FFFFFF",
            "highlight_color": "#FFE600",
            "outline_color": "#000000",
            "shadow_color": "#000000",
        }
        self._color_buttons: dict[str, QPushButton] = {}
        color_row = QHBoxLayout()
        color_row.setSpacing(8)
        for name, label in (
            ("primary_color", "Texto"),
            ("highlight_color", "Resaltado"),
            ("outline_color", "Contorno"),
            ("shadow_color", "Sombra"),
        ):
            color_control = QWidget()
            color_layout = QHBoxLayout(color_control)
            color_layout.setContentsMargins(0, 0, 0, 0)
            color_layout.setSpacing(7)
            color_layout.addWidget(QLabel(label))
            button = QPushButton()
            button.setFixedSize(36, 30)
            button.clicked.connect(lambda _checked=False, key=name: self._choose_color(key))
            self._color_buttons[name] = button
            self._set_color_button(name)
            color_layout.addWidget(button)
            color_row.addWidget(color_control)
        color_row.addStretch(1)
        self._uppercase = QCheckBox("Usar mayúsculas")
        color_row.addWidget(self._uppercase)
        layout.addLayout(color_row)
        return widget

    def _environment_card(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        self._environment_details = QLabel("Verificando dependencias…")
        self._environment_details.setObjectName("environmentDetails")
        self._environment_details.setWordWrap(True)
        layout.addWidget(self._environment_details)
        refresh = QPushButton("Volver a comprobar")
        refresh.setObjectName("secondary")
        refresh.clicked.connect(self._refresh_environment)
        layout.addWidget(refresh, alignment=Qt.AlignmentFlag.AlignRight)
        return widget

    def _restore_preferences(self) -> None:
        folder = self._settings.value("last_folder", str(Path.home()), type=str)
        self._last_folder = Path(folder).expanduser()
        settings_version = self._settings.value("settings_version", 0, type=int)
        if settings_version < 2:
            old_compute_type = self._settings.value("compute_type", "int8", type=str)
            if old_compute_type == "default":
                self._settings.setValue("compute_type", "int8")
            self._settings.setValue("settings_version", 2)
        self._model.setCurrentText(
            self._settings.value("model", "small", type=str)
        )
        self._select_combo_data(self._language, self._settings.value("language", "", type=str))
        self._select_combo_data(self._device, self._settings.value("device", "auto", type=str))
        self._compute_type.setCurrentText(self._settings.value("compute_type", "int8", type=str))
        self._select_combo_data(self._style, self._settings.value("style", "tiktok_pop", type=str))
        self._font.setCurrentFont(
            QFont(self._settings.value("font_family", self._font.currentFont().family(), type=str))
        )
        self._font_size.setValue(self._settings.value("font_size", 64, type=int))
        self._select_combo_data(self._position, self._settings.value("position", "bottom", type=str))
        self._uppercase.setChecked(self._settings.value("uppercase", False, type=bool))
        for name in self._colors:
            color = self._settings.value(f"colors/{name}", self._colors[name], type=str)
            self._colors[name] = color if QColor(color).isValid() else self._colors[name]
            self._set_color_button(name)

    def _connect_preferences(self) -> None:
        self._model.currentTextChanged.connect(self._save_preferences)
        self._language.currentIndexChanged.connect(self._save_preferences)
        self._device.currentIndexChanged.connect(self._save_preferences)
        self._compute_type.currentIndexChanged.connect(self._save_preferences)
        self._style.currentIndexChanged.connect(self._save_preferences)
        self._font.currentFontChanged.connect(self._save_preferences)
        self._font_size.valueChanged.connect(self._save_preferences)
        self._position.currentIndexChanged.connect(self._save_preferences)
        self._uppercase.toggled.connect(self._save_preferences)
        for control, signal in (
            (self._style, self._style.currentIndexChanged),
            (self._font, self._font.currentFontChanged),
            (self._font_size, self._font_size.valueChanged),
            (self._position, self._position.currentIndexChanged),
            (self._uppercase, self._uppercase.toggled),
        ):
            signal.connect(self._refresh_style_preview)

    @staticmethod
    def _select_combo_data(combo: QComboBox, data: str) -> None:
        index = combo.findData(data)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _save_preferences(self, *_args) -> None:
        if not hasattr(self, "_settings") or not hasattr(self, "_model"):
            return
        self._settings.setValue("model", self._model.currentText())
        self._settings.setValue("language", self._language.currentData())
        self._settings.setValue("device", self._device.currentData())
        self._settings.setValue("compute_type", self._compute_type.currentText())
        self._settings.setValue("style", self._style.currentData())
        self._settings.setValue("font_family", self._font.currentFont().family())
        self._settings.setValue("font_size", self._font_size.value())
        self._settings.setValue("position", self._position.currentData())
        self._settings.setValue("uppercase", self._uppercase.isChecked())
        for name, color in self._colors.items():
            self._settings.setValue(f"colors/{name}", color)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._thread and self._thread.isRunning():
            QMessageBox.information(
                self,
                "Generación en curso",
                "Espera a que termine la generación antes de cerrar la aplicación.",
            )
            event.ignore()
            return
        self._save_preferences()
        super().closeEvent(event)

    @staticmethod
    def _combo() -> QComboBox:
        combo = QComboBox()
        combo.setMinimumWidth(170)
        combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return combo

    @staticmethod
    def _field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("fieldLabel")
        return label

    def _refresh_environment(self) -> None:
        self._report = self._environment.diagnose()
        statuses = (
            self._report.faster_whisper,
            self._report.ffmpeg,
            self._report.ffprobe,
        )
        lines = [
            f"{'✓' if item.available else '✕'}  {item.name}: {item.details}"
            for item in statuses
        ]
        lines.append(f"Python {self._report.python_version} · {self._report.python_executable}")
        missing = [item for item in statuses if not item.available]
        if missing:
            lines.extend(("", "Solución: " + " · ".join(item.remedy for item in missing)))
        self._environment_details.setText("\n".join(lines))
        self._environment_details.setToolTip("\n".join(lines))
        self._environment_badge.setText("ENTORNO LISTO" if self._report.ready else "FALTAN DEPENDENCIAS")
        self._environment_badge.setProperty("ready", self._report.ready)
        self._environment_badge.style().unpolish(self._environment_badge)
        self._environment_badge.style().polish(self._environment_badge)
        if not self._report.ready and self._video:
            self._status.setText("Corrige las dependencias indicadas antes de generar.")
        self._update_generate_button()

    @Slot()
    def _select_video(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Seleccionar video",
            str(self._last_folder),
            "Videos (*.mp4 *.mov *.mkv *.m4v *.avi *.webm)",
        )
        if path:
            self._set_video(Path(path))

    def _set_video(self, path: Path) -> None:
        if path.suffix.lower() not in _VIDEO_EXTENSIONS or not path.is_file():
            QMessageBox.warning(self, "Video no válido", "Selecciona un archivo de video existente.")
            return
        self._video = path
        self._transcript = None
        self._metadata = None
        self._workspace = None
        self._reusable_transcript = None
        self._transcript_status.setText("Todavía no hay una transcripción para este video.")
        self._last_folder = path.parent
        self._settings.setValue("last_folder", str(path.parent))
        self._video_label.setText(path.name)
        self._elide_video_name()
        self._video_label.setToolTip(str(path))
        self._status.setText(f"Video seleccionado · {path.name}")
        self._show_step(0)
        self._update_generate_button()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide_video_name()

    def _elide_video_name(self) -> None:
        if self._video:
            self._video_label.setText(
                QFontMetrics(self._video_label.font()).elidedText(
                    self._video.name,
                    Qt.TextElideMode.ElideMiddle,
                    max(0, self._video_label.width()),
                )
            )

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].isLocalFile():
            path = Path(urls[0].toLocalFile())
            if path.suffix.lower() in _VIDEO_EXTENSIONS:
                event.acceptProposedAction()
                return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].isLocalFile():
            self._set_video(Path(urls[0].toLocalFile()))
            event.acceptProposedAction()

    def _choose_color(self, name: str) -> None:
        selected = QColorDialog.getColor(QColor(self._colors[name]), self, "Seleccionar color")
        if selected.isValid():
            self._colors[name] = selected.name().upper()
            self._set_color_button(name)
            self._refresh_style_preview()

    def _set_color_button(self, name: str) -> None:
        button = self._color_buttons[name]
        foreground = "#111111" if name in {"primary_color", "highlight_color"} else "#FFFFFF"
        button.setStyleSheet(
            f"QPushButton {{ background: {self._colors[name]}; color: {foreground}; }}"
        )
        button.setToolTip(f"{name}: {self._colors[name]}")

    def _update_generate_button(self) -> None:
        ready = bool(self._video and self._report and self._report.ready)
        busy = self._thread is not None
        self._generate.setEnabled(ready and not busy)
        self._next.setEnabled(bool(self._video) and not busy)
        self._back.setEnabled(self._current_step > 0 and not busy)
        self._generate.setVisible(self._current_step == 3)
        self._next.setVisible(self._current_step < 3)
        if not self._report or not self._report.ready:
            return
        if not self._video:
            self._status.setText("Selecciona o arrastra un video para comenzar.")

    def _show_step(self, step: int) -> None:
        self._current_step = min(3, max(0, step))
        self._pages.setCurrentIndex(self._current_step)
        names = ("VIDEO", "TRANSCRIPCIÓN", "ESTILO", "GENERACIÓN")
        self._step_heading.setText(f"PASO {self._current_step + 1} DE 4 · {names[self._current_step]}")
        self._back.setEnabled(self._current_step > 0 and self._thread is None)
        self._next.setVisible(self._current_step < 3)
        self._generate.setVisible(self._current_step == 3)
        self._next.setText(
            "Transcribir" if self._current_step == 1 and self._transcript is None
            else "Siguiente"
        )
        self._update_generate_button()

    @Slot()
    def _previous_step(self) -> None:
        if self._current_step:
            self._show_step(self._current_step - 1)

    @Slot()
    def _next_step(self) -> None:
        if self._current_step == 0:
            if self._video:
                self._show_step(1)
            return
        if self._current_step == 1:
            if self._transcript is None:
                self._start_transcription()
            else:
                self._show_step(2)
            return
        if self._current_step == 2:
            self._refresh_style_preview()
            self._generation_summary.setText(
                f"Video: {self._video.name if self._video else '—'}\n"
                f"Palabras: {len(self._transcript.words) if self._transcript else 0}\n"
                f"Estilo: {self._style.currentText()}\n\n"
                "Se crearán los captions y su layout. No se exportará ASS/SRT ni se renderizará video."
            )
            self._show_step(3)

    def _options(self) -> TranscriptionOptions:
        return TranscriptionOptions(
            model=self._model.currentText(),
            language=self._language.currentData() or None,
            device=self._device.currentData(),
            compute_type=self._compute_type.currentText(),
        )

    def _style_config(self) -> CaptionStyleConfig:
        return CaptionStyleConfig(
            font_family=self._font.currentFont().family(),
            font_size=self._font_size.value(),
            position=self._position.currentData(),
            uppercase=self._uppercase.isChecked(),
            **self._colors,
        )

    def _output_root(self) -> Path:
        return Path.home() / "Movies" / "Animated Caption Studio"

    def _start_transcription(self) -> None:
        if not self._video or not self._report or not self._report.ready:
            return
        if self._workspace is None:
            try:
                self._workspace = self._service.create_workspace(
                    self._output_root(), self._video
                )
            except OSError as exc:
                logger.exception("No se pudo crear el espacio de trabajo")
                QMessageBox.critical(
                    self,
                    "No se pudo iniciar la transcripción",
                    f"No se pudo crear la carpeta del proyecto:\n{exc}",
                )
                return
        if self._reusable_transcript is None:
            candidates = self._service.reusable_transcripts(
                self._video,
                self._output_root(),
                tuple(Path(item) for item in self._recent_project_paths()),
            )
            if candidates:
                answer = QMessageBox.question(
                    self,
                    "Transcripción existente",
                    "Ya existe transcript.json para este video. ¿Quieres reutilizarlo?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if answer == QMessageBox.StandardButton.Yes:
                    self._reusable_transcript = candidates[0]

        self._pending_operation = "transcription"
        self._next.setEnabled(False)
        self._generate.setEnabled(False)
        self._progress.setValue(0)
        self._thread = QThread(self)
        self._worker = PipelineWorker(
            self._service.transcribe_to_workspace,
            (
                self._video,
                self._workspace,
                self._options(),
                self._reusable_transcript,
            ),
        )
        self._start_worker()

    @Slot()
    def _start_layout(self) -> None:
        if (
            not self._video
            or self._transcript is None
            or self._metadata is None
            or self._workspace is None
            or self._thread is not None
        ):
            return
        if (self._workspace / "project.acs").is_file():
            try:
                workspace = self._service.create_workspace(
                    self._output_root(), self._video
                )
                self._service.save_transcript(self._transcript, workspace)
            except OSError as exc:
                logger.exception("No se pudo crear una nueva revisión del proyecto")
                QMessageBox.critical(
                    self,
                    "No se pudo preparar el proyecto",
                    f"No se pudo guardar la transcripción para una nueva revisión:\n{exc}",
                )
                return
            self._workspace = workspace
        self._pending_operation = "layout"
        self._generate.setEnabled(False)
        self._progress.setValue(0)
        self._worker = PipelineWorker(
            self._service.build_editor_project,
            (
                self._video,
                self._transcript,
                self._options(),
                self._style.currentData(),
                self._style_config(),
                self._metadata,
            ),
        )
        self._start_worker()

    def _start_worker(self) -> None:
        assert self._worker is not None
        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.succeeded.connect(self._on_operation_success)
        self._worker.failed.connect(self._on_failure)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._on_finished)
        self._thread.start()

    @Slot(str, int)
    def _on_progress(self, message: str, value: int) -> None:
        self._status.setText(message)
        self._progress.setValue(value)

    @Slot(object)
    def _on_operation_success(self, result: object) -> None:
        self._progress.setValue(100)
        if self._pending_operation == "transcription":
            self._transcript, self._metadata, transcript_path = result
            reused = self._reusable_transcript is not None
            self._transcript_status.setText(
                f"{'Transcripción reutilizada' if reused else 'Transcripción lista'} · "
                f"{len(self._transcript.words)} palabras guardadas en {transcript_path}"
            )
            self._refresh_style_preview()
            self._status.setText("Transcripción lista. Puedes continuar al estilo.")
            self._show_step(2)
            return

        if self._pending_operation == "layout":
            project_path = self._workspace / "project.acs"
            editor = CaptionEditorService(result)
            try:
                editor.save(project_path)
            except OSError as exc:
                logger.exception("No se pudo guardar el proyecto inicial")
                QMessageBox.critical(self, "Error al guardar", str(exc))
                return
            self._add_recent_project(project_path)
            self._status.setText("Layout listo · abriendo Caption Editor.")
            self._show_editor(editor, project_path, self._metadata)

    @Slot()
    def _open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Abrir proyecto de captions",
            str(self._last_folder),
            "Proyectos Caption Studio (*.acs)",
        )
        if not path:
            return
        self._open_project_path(Path(path))

    def _recent_project_paths(self) -> list[str]:
        value = self._settings.value("recent_projects", [])
        paths = [value] if isinstance(value, str) else list(value or [])
        return [str(Path(item).expanduser()) for item in paths]

    def _add_recent_project(self, path: Path) -> None:
        paths = [str(path)] + [
            item for item in self._recent_project_paths() if Path(item) != path
        ]
        paths = [item for item in paths if Path(item).is_file()][:10]
        self._settings.setValue("recent_projects", paths)

    def _show_recent_projects(self) -> None:
        menu = QMenu(self)
        paths = [Path(item) for item in self._recent_project_paths()]
        existing = [path for path in paths if path.is_file()]
        if not existing:
            action = menu.addAction("No hay proyectos recientes")
            action.setEnabled(False)
        else:
            for path in existing:
                action = menu.addAction(f"{path.parent.name} — {path.name}")
                action.setToolTip(str(path))
                action.triggered.connect(
                    lambda _checked=False, project_path=path: self._open_project_path(
                        project_path
                    )
                )
        menu.exec(self.mapToGlobal(self.rect().topLeft()))

    def _open_project_path(self, project_path: Path) -> None:
        try:
            editor = CaptionEditorService.load(project_path)
            project = editor.project
            metadata = project.video_metadata or self._service.video_metadata(
                project.source_video
            )
            if project.video_metadata is None:
                editor = CaptionEditorService(
                    replace(project, video_metadata=metadata)
                )
            self._show_editor(editor, project_path, metadata)
        except (CaptionStudioError, OSError, ValueError) as exc:
            logger.exception("No se pudo abrir el proyecto %s", project_path.name)
            QMessageBox.critical(self, "No se pudo abrir el proyecto", str(exc))

    def _show_editor(
        self,
        editor: CaptionEditorService,
        project_path: Path,
        metadata: VideoMetadata | None = None,
    ) -> None:
        metadata = metadata or editor.project.video_metadata
        if metadata is None:
            metadata = self._service.video_metadata(editor.project.source_video)
            editor = CaptionEditorService(
                replace(editor.project, video_metadata=metadata)
            )
        self._preview_window = CaptionEditorWindow(
            editor, metadata, project_path, self._service, self
        )
        self._add_recent_project(project_path)
        self._preview_window.show()

    def _refresh_style_preview(self, *_args) -> None:
        if self._transcript is None:
            text = "La vista previa usará palabras reales del transcript."
        else:
            words = [word.text for word in self._transcript.words[:6]]
            if not words:
                text = "No se detectaron palabras en la transcripción."
            else:
                behavior = StyleRegistry().get(self._style.currentData()).behavior
                rendered_words = [escape(word) for word in words]
                if behavior != "minimal":
                    rendered_words[0] = (
                        f'<span style="color:{self._colors["highlight_color"]}">'
                        f"{rendered_words[0]}</span>"
                    )
                text = " ".join(rendered_words)
        self._style_preview.setText(text)
        font = QFont(self._font.currentFont())
        font.setPointSize(max(10, min(42, round(self._font_size.value() * 0.48))))
        font.setBold(self._style.currentData() == "bold_highlight")
        self._style_preview.setFont(font)
        self._style_preview.setStyleSheet(
            f"QLabel {{ color: {self._colors['primary_color']}; "
            "background-color: #20232b; border-radius: 8px; padding: 16px; }"
        )

    @Slot(str)
    def _on_failure(self, message: str) -> None:
        label = (
            "La transcripción no se completó."
            if self._pending_operation == "transcription"
            else "No se pudo preparar el proyecto."
        )
        self._status.setText(label)
        QMessageBox.critical(self, "Error del flujo de trabajo", message)

    @Slot()
    def _on_finished(self) -> None:
        self._worker = None
        self._thread = None
        self._update_generate_button()
