"""Empaqueta la aplicación como .app en macOS usando PyInstaller."""

from __future__ import annotations

import platform
import subprocess
import sys


def main() -> int:
    if sys.version_info < (3, 11):
        raise SystemExit("Se requiere Python 3.11 o superior.")
    if platform.system() != "Darwin":
        raise SystemExit("Este script solo empaqueta la aplicación en macOS.")
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--windowed",
            "--name",
            "Animated Caption Studio",
            "--collect-all",
            "faster_whisper",
            "app/main.py",
        ],
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
