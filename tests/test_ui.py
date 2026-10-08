import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QComboBox, QFontComboBox, QPushButton

from app.rendering.ffmpeg import FFmpegService
from app.services.environment import EnvironmentService
from app.ui.main_window import MainWindow


class MainWindowLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])
        cls.settings = QSettings("Animated Caption Studio", "Animated Caption Studio")
        cls.previous_settings = {
            key: cls.settings.value(key) for key in cls.settings.allKeys()
        }
        cls.settings.clear()
        cls.window = MainWindow(
            None, EnvironmentService(FFmpegService())
        )
        cls.window.resize(820, 620)
        cls.window.show()
        cls.application.processEvents()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.window.close()
        cls.application.processEvents()
        cls.settings.clear()
        for key, value in cls.previous_settings.items():
            cls.settings.setValue(key, value)
        cls.settings.sync()

    def test_controls_keep_usable_width_at_minimum_window_size(self) -> None:
        combos = self.window.findChildren(QComboBox)
        fonts = self.window.findChildren(QFontComboBox)
        self.assertGreaterEqual(self.window.width(), 820)
        self.assertTrue(combos)
        self.assertTrue(fonts)
        self.assertTrue(all(combo.width() >= combo.minimumWidth() for combo in combos))
        self.assertGreaterEqual(fonts[0].width(), fonts[0].minimumWidth())

    def test_close_application_button_closes_the_window(self) -> None:
        close_button = self.window.findChild(QPushButton, "closeApplication")
        self.assertIsNotNone(close_button)
        close_button.click()
        self.application.processEvents()
        self.assertFalse(self.window.isVisible())
        self.window.show()
        self.application.processEvents()

    def test_generation_requires_a_video_even_with_ready_dependencies(self) -> None:
        self.assertTrue(self.window._report.ready)
        self.assertFalse(self.window._generate.isEnabled())
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "clip.mp4"
            video.touch()
            self.window._set_video(video)
        self.assertTrue(self.window._generate.isEnabled())

    def test_migrates_previous_default_compute_type_to_cpu_int8(self) -> None:
        self.window.close()
        self.window._settings.setValue("compute_type", "default")
        self.window._settings.setValue("settings_version", 1)
        self.window = MainWindow(None, EnvironmentService(FFmpegService()))
        self.assertEqual(self.window._compute_type.currentText(), "int8")
        self.assertEqual(self.window._settings.value("settings_version", type=int), 2)


if __name__ == "__main__":
    unittest.main()
