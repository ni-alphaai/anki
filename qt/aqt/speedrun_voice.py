# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""On-device voice transcription for Speedrun self-explanations.

Two modes, both on-device (nothing is transmitted):

- ``LiveTranscriber`` (preferred): live "words as you speak" streaming via a
  lightweight ``sounddevice`` mic stream + faster-whisper -- interim text from a
  small model (tiny.en) re-run over the growing buffer while speaking, then a
  clean final pass (base.en) on stop. This deliberately avoids RealtimeSTT,
  which hard-depends on PyTorch + CUDA (multiple GB, pointless on macOS).
- ``transcribe`` (fallback): a one-shot faster-whisper pass over a recorded
  file, used when the live mic stream is unavailable.

HuggingFace's "unauthenticated requests" notice is a plain log line, but Anki's
ErrorHandler redirects stderr into its error dialog, so it would pop up as a
fake error. We silence HF logging/progress *before* the model libraries load so
nothing noisy reaches stderr. No token is required.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Callable

# Must be set before huggingface_hub / faster-whisper import so their download
# path stays quiet (otherwise the notice reaches Anki's stderr error dialog).
os.environ.setdefault("HF_HUB_VERBOSITY", "error")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

# Small English models keep the download light and CPU transcription fast.
_FINAL_MODEL = "base.en"
_REALTIME_MODEL = "tiny.en"
_SAMPLE_RATE = 16000

_model = None  # base.en (final / batch)
_rt_model = None  # tiny.en (interim)


def _quiet_hf() -> None:
    """Force huggingface_hub logging to error level (belt-and-suspenders)."""
    try:
        from huggingface_hub.utils import logging as hf_logging  # type: ignore

        hf_logging.set_verbosity_error()
    except Exception:
        pass


def _load(name: str):
    """Load a faster-whisper model, preferring the local cache (no HF notice)."""
    _quiet_hf()
    from faster_whisper import WhisperModel  # type: ignore

    try:
        return WhisperModel(name, device="cpu", compute_type="int8", local_files_only=True)
    except Exception:
        return WhisperModel(name, device="cpu", compute_type="int8")


# --- batch fallback ---------------------------------------------------------


def is_available() -> bool:
    """True if faster-whisper (the batch fallback) can be imported."""
    try:
        import faster_whisper  # type: ignore  # noqa: F401

        return True
    except Exception:
        return False


def _get_model():
    global _model
    if _model is None:
        _model = _load(_FINAL_MODEL)
    return _model


def transcribe(path: str) -> str:
    """Transcribe an audio file to text. Blocking; run off the UI thread."""
    model = _get_model()
    segments, _info = model.transcribe(path, beam_size=1)
    return " ".join(seg.text.strip() for seg in segments).strip()


def _transcribe_audio(model, audio) -> str:
    segments, _info = model.transcribe(audio, beam_size=1)
    return " ".join(seg.text.strip() for seg in segments).strip()


# --- live streaming ---------------------------------------------------------


class LiveTranscriber:
    """Live transcription via a sounddevice mic stream + faster-whisper.

    Captures mic frames into a growing buffer; every ~1.5s it re-transcribes the
    buffer with tiny.en and emits the interim text (so words appear as you
    speak). On stop() it runs a clean base.en pass and emits the final text. All
    work runs off the UI thread; callbacks fire from worker threads, so the
    caller must marshal them to the UI thread.
    """

    _INTERIM_INTERVAL = 1.5

    def __init__(self) -> None:
        self._stream = None
        self._worker: threading.Thread | None = None
        self._buf: list = []
        self._lock = threading.Lock()
        self._running = False
        self._on_interim: Callable[[str], None] | None = None
        self._on_final: Callable[[str], None] | None = None
        self._on_ready: Callable[[], None] | None = None
        self._on_error: Callable[[str], None] | None = None

    @staticmethod
    def available() -> bool:
        try:
            import numpy  # type: ignore  # noqa: F401
            import sounddevice  # type: ignore  # noqa: F401
            import faster_whisper  # type: ignore  # noqa: F401

            return True
        except Exception:
            return False

    def start(
        self,
        on_interim: Callable[[str], None],
        on_final: Callable[[str], None],
        on_ready: Callable[[], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self._on_interim = on_interim
        self._on_final = on_final
        self._on_ready = on_ready
        self._on_error = on_error
        self._running = True
        self._buf = []
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def _run(self) -> None:
        global _rt_model
        try:
            import sounddevice as sd  # type: ignore

            if _rt_model is None:
                _rt_model = _load(_REALTIME_MODEL)

            def callback(indata, frames, time_info, status) -> None:  # noqa: ANN001
                with self._lock:
                    self._buf.append(indata.copy().reshape(-1))

            self._stream = sd.InputStream(
                samplerate=_SAMPLE_RATE, channels=1, dtype="float32", callback=callback
            )
            self._stream.start()
        except Exception as exc:
            self._running = False
            if self._on_error:
                self._on_error(str(exc))
            return

        if self._on_ready:
            self._on_ready()

        elapsed = 0.0
        while self._running:
            time.sleep(0.2)
            elapsed += 0.2
            if elapsed >= self._INTERIM_INTERVAL:
                elapsed = 0.0
                self._interim_tick()

    def _snapshot(self):
        import numpy as np  # type: ignore

        with self._lock:
            if not self._buf:
                return None
            return np.concatenate(self._buf)

    def _interim_tick(self) -> None:
        audio = self._snapshot()
        # need at least ~0.5s of audio to be worth transcribing
        if audio is None or len(audio) < _SAMPLE_RATE // 2:
            return
        try:
            text = _transcribe_audio(_rt_model, audio)
        except Exception:
            return
        if self._running and self._on_interim and text:
            self._on_interim(text)

    def stop(self) -> None:
        """Stop capturing and run a clean final pass (main thread)."""
        self._running = False
        self._close_stream()
        threading.Thread(target=self._finalize, daemon=True).start()

    def _finalize(self) -> None:
        audio = self._snapshot()
        text = ""
        if audio is not None and len(audio) > _SAMPLE_RATE // 3:
            try:
                text = _transcribe_audio(_get_model(), audio)
            except Exception:
                text = ""
        if self._on_final:
            self._on_final(text)

    def _close_stream(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

    def shutdown(self) -> None:
        """Release the mic without a final pass."""
        self._running = False
        self._close_stream()
