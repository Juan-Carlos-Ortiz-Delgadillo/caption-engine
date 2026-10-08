import tempfile
import unittest
import wave
from pathlib import Path

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
