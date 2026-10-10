import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from app.core.models import TranscriptionOptions
from app.transcription.faster_whisper_engine import FasterWhisperEngine
from faster_whisper.audio import decode_audio


class FasterWhisperAudioTests(unittest.TestCase):
    def test_audio_decoder_opens_pcm_wav_with_supported_pyav_api(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "audio sample.wav"
            with wave.open(str(audio_path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(16_000)
                audio.writeframes(b"\0\0" * 3_200)

            samples = decode_audio(str(audio_path), sampling_rate=16_000)

        self.assertEqual(len(samples), 3_200)

    def test_model_loading_is_restricted_to_local_cache(self) -> None:
        class Model:
            def transcribe(self, *_args, **_kwargs):
                word = type(
                    "WordData",
                    (),
                    {"word": " local", "start": 0.0, "end": 0.4, "probability": 0.9},
                )()
                segment = type(
                    "Segment",
                    (),
                    {"start": 0.0, "end": 0.4, "words": [word]},
                )()
                info = type(
                    "Info",
                    (),
                    {"duration": 0.4, "language": "en", "language_probability": 1.0},
                )()
                return iter([segment]), info

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.mp4"
            source.touch()
            with patch("faster_whisper.WhisperModel", return_value=Model()) as factory:
                result = FasterWhisperEngine().transcribe(
                    source, TranscriptionOptions(device="cpu")
                )
        self.assertEqual(result.words[0].text, "local")
        self.assertTrue(factory.call_args.kwargs["local_files_only"])

    def test_spanish_transcription_normalizes_unicode_and_removes_foreign_letters(self) -> None:
        class Model:
            def transcribe(self, *_args, **_kwargs):
                accented_word = type(
                    "WordData",
                    (),
                    {
                        "word": " man\u0303ana",
                        "start": 0.0,
                        "end": 0.2,
                        "probability": 0.9,
                    },
                )()
                contaminated_word = type(
                    "WordData",
                    (),
                    {
                        "word": " pendientesạẠồị",
                        "start": 0.2,
                        "end": 0.4,
                        "probability": 0.9,
                    },
                )()
                segment = type(
                    "Segment",
                    (),
                    {
                        "start": 0.0,
                        "end": 0.4,
                        "words": [accented_word, contaminated_word],
                    },
                )()
                info = type(
                    "Info",
                    (),
                    {"duration": 0.4, "language": "es", "language_probability": 1.0},
                )()
                return iter([segment]), info

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.mp4"
            source.touch()
            with (
                patch("faster_whisper.WhisperModel", return_value=Model()),
                self.assertLogs(
                    "app.transcription.faster_whisper_engine", level="WARNING"
                ),
            ):
                result = FasterWhisperEngine().transcribe(
                    source, TranscriptionOptions(device="cpu")
                )

        self.assertEqual(
            tuple(word.text for word in result.words), ("mañana", "pendientes")
        )
