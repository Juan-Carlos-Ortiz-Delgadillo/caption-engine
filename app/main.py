"""Punto de entrada: composición de dependencias e inicio de Qt."""

import sys

from PySide6.QtWidgets import QApplication

from app.ass.generator import ASSGenerator
from app.captions.segmenter import CaptionSegmenter
from app.core.logger import configure_logging
from app.rendering.ffmpeg import FFmpegService
from app.services.environment import EnvironmentService
from app.services.pipeline import CaptionApplicationService
from app.srt.generator import SRTGenerator
from app.transcription.faster_whisper_engine import FasterWhisperEngine
from app.ui.main_window import MainWindow


def main() -> int:
    configure_logging()
    application = QApplication(sys.argv)
    application.setApplicationName("Animated Caption Studio")
    ffmpeg = FFmpegService()
    service = CaptionApplicationService(
        transcription_engine=FasterWhisperEngine(),
        segmenter=CaptionSegmenter(),
        ass_generator=ASSGenerator(),
        srt_generator=SRTGenerator(),
        ffmpeg=ffmpeg,
    )
    environment = EnvironmentService(ffmpeg)
    window = MainWindow(service, environment)
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
