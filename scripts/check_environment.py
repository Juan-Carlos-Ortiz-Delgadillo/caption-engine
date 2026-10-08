"""Comprueba Python, faster-whisper, FFmpeg/libass y ffprobe."""

import sys
from pathlib import Path

project_root = Path(__file__).resolve().parents[1]
if project_root not in map(Path, sys.path):
    sys.path.insert(0, str(project_root))

from app.rendering.ffmpeg import FFmpegService
from app.services.environment import EnvironmentService


def main() -> int:
    report = EnvironmentService(FFmpegService()).diagnose()
    print("========================================")
    print("ANIMATED CAPTION STUDIO")
    print("DIAGNÓSTICO DEL ENTORNO")
    print("========================================")
    print(f"\nPython\n✓ {report.python_version}\n  {report.python_executable}")
    for item in (report.faster_whisper, report.ffmpeg, report.ffprobe):
        icon = "✓" if item.available else "✕"
        print(f"\n{item.name}\n{icon} {item.details}")
        if not item.available:
            print(f"Solución: {item.remedy}")
    print("\n========================================")
    print("ENTORNO LISTO" if report.ready else "ENTORNO INCOMPLETO")
    print("========================================")
    return 0 if report.ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
