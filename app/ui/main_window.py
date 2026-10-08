"""Interfaz principal adaptable y arranque asíncrono del flujo de generación."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, QThread, QUrl, Qt, Signal, Slot
from PySide6.QtGui import (
    QColor,
    QCloseEvent,
    QDesktopServices,
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
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.core.exceptions import CaptionStudioError
from app.core.models import CaptionStyleConfig, TranscriptionOptions
from app.services.environment import EnvironmentReport, EnvironmentService
from app.services.pipeline import CaptionApplicationService, PipelineResult
from app.styles.registry import StyleRegistry
from app.ui.theme import STYLESHEET

logger = logging.getLogger(__name__)
_VIDEO_EXTENSIONS = {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".webm"}


class PipelineWorker(QObject):
    progress = Signal(str, int)
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, service: CaptionApplicationService, args: tuple) -> None:
        super().__init__()
        self._service = service
        self._args = args

    @Slot()
    def run(self) -> None:
        try:
            result = self._service.generate(*self._args, progress=self.progress.emit)
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
        self._thread: QThread | None = None
        self._worker: PipelineWorker | None = None
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
        header.addStretch(1)
        self._environment_badge = QLabel("Verificando entorno…")
        self._environment_badge.setObjectName("environmentBadge")
        header.addWidget(self._environment_badge)
        outer.addLayout(header)

        scroll = QScrollArea()
        scroll.setObjectName("contentScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        page = QWidget()
        page.setObjectName("contentPage")
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(2, 2, 8, 8)
        page_layout.setSpacing(14)
        page_layout.addWidget(self._card("1 · VIDEO", self._video_card()))
        page_layout.addWidget(self._card("2 · TRANSCRIPCIÓN", self._transcription_card()))
        page_layout.addWidget(self._card("3 · ESTILO", self._style_card()))
        page_layout.addWidget(self._card("ENTORNO DE EJECUCIÓN", self._environment_card()))
        page_layout.addStretch(1)
        scroll.setWidget(page)
        outer.addWidget(scroll, 1)

        generation_label = QLabel("4 · GENERACIÓN")
        generation_label.setObjectName("section")
        outer.addWidget(generation_label)
        self._status = QLabel("Selecciona o arrastra un video para comenzar.")
        self._status.setObjectName("status")
        self._status.setWordWrap(True)
        outer.addWidget(self._status)
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        outer.addWidget(self._progress)
        self._generate = QPushButton("Generar subtítulos y video")
        self._generate.setObjectName("primary")
        self._generate.setMinimumHeight(46)
        self._generate.clicked.connect(self._start)
        self._close_button = QPushButton("Cerrar aplicación")
        self._close_button.setObjectName("closeApplication")
        self._close_button.clicked.connect(self.close)
        actions = QHBoxLayout()
        actions.setSpacing(10)
        actions.addWidget(self._generate, 1)
        actions.addWidget(self._close_button)
        outer.addLayout(actions)
        self.setCentralWidget(root)

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
        self._last_folder = path.parent
        self._settings.setValue("last_folder", str(path.parent))
        self._video_label.setText(path.name)
        self._elide_video_name()
        self._video_label.setToolTip(str(path))
        self._status.setText(f"Video seleccionado · {path.name}")
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
        if not self._report or not self._report.ready:
            return
        if not self._video:
            self._status.setText("Selecciona o arrastra un video para comenzar.")

    @Slot()
    def _start(self) -> None:
        if not self._video or not self._report or not self._report.ready:
            return
        options = TranscriptionOptions(
            model=self._model.currentText(),
            language=self._language.currentData() or None,
            device=self._device.currentData(),
            compute_type=self._compute_type.currentText(),
        )
        config = CaptionStyleConfig(
            font_family=self._font.currentFont().family(),
            font_size=self._font_size.value(),
            position=self._position.currentData(),
            uppercase=self._uppercase.isChecked(),
            **self._colors,
        )
        output_root = Path.home() / "Movies" / "Animated Caption Studio"
        self._generate.setEnabled(False)
        self._progress.setValue(0)
        self._thread = QThread(self)
        self._worker = PipelineWorker(
            self._service,
            (self._video, output_root, options, self._style.currentData(), config),
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.succeeded.connect(self._on_success)
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
    def _on_success(self, result: PipelineResult) -> None:
        self._status.setText(f"Finalizado · {result.output_directory}")
        self._progress.setValue(100)
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Generación completada")
        dialog.setText("Se generaron el video, ASS, SRT, transcript.json y project.acs.")
        actions = (
            ("Abrir video", result.video),
            ("Abrir carpeta", result.output_directory),
            ("Abrir ASS", result.ass),
            ("Abrir SRT", result.srt),
        )
        buttons = [
            (dialog.addButton(label, QMessageBox.ButtonRole.ActionRole), path)
            for label, path in actions
        ]
        dialog.addButton("Cerrar", QMessageBox.ButtonRole.RejectRole)
        dialog.exec()
        for button, path in buttons:
            if dialog.clickedButton() is button:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
                break

    @Slot(str)
    def _on_failure(self, message: str) -> None:
        self._status.setText("La generación no se completó.")
        QMessageBox.critical(self, "Error de generación", message)

    @Slot()
    def _on_finished(self) -> None:
        self._worker = None
        self._thread = None
        self._update_generate_button()
