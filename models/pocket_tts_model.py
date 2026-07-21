"""
Pocket TTS Model Wrapper — CPU-friendly lightweight TTS (Kyutai)
Wrapper per Kyutai Pocket TTS con supporto voice cloning e streaming.
Stessa interfaccia API di VoxCPMTTSPipeline per compatibilità drop-in.

Requisiti: pip install pocket-tts scipy
Python ≥ 3.10, PyTorch ≥ 2.5 (CPU-only ok)
"""

import os
import re
import numpy as np
import torch
from typing import List, Optional, Tuple, Generator, Dict
from loguru import logger


def _ensure_pocket_tts_installed():
    """Verifica che pocket-tts sia installato."""
    try:
        from pocket_tts import TTSModel  # noqa: F401
    except ImportError:
        raise RuntimeError(
            "pocket-tts non installato. Esegui: pip install pocket-tts scipy\n"
            "Requisiti: Python ≥ 3.10, PyTorch ≥ 2.5 (CPU ok)"
        )


# Voci built-in del catalogo Pocket TTS (non richiedono HF login / voice cloning)
CATALOG_VOICES = frozenset({
    # EN
    "cosette", "marius", "javert", "alba", "jean", "anna", "vera",
    "fantine", "charles", "paul", "eponine", "azelma", "george",
    "mary", "jane", "michael", "eve", "bill_boerst", "peter_yearsley",
    "stuart_bell", "caro_davy",
    # IT
    "giovanni",
    # ES
    "lola",
    # DE
    "juergen",
    # PT
    "rafael",
    # FR
    "estelle",
})


def _is_catalog_voice(ref: str) -> bool:
    """Check if ref is a catalog voice name (not a file path or URL)."""
    return ref in CATALOG_VOICES


class PocketTTSPipeline:
    """Pocket TTS streaming pipeline wrapper.

    Rilevamento automatico voci:
      - Nomi catalog (es. "giovanni", "alba") → NO HF login necessario
      - File .wav/.safetensors → richiede HF login + termini accettati
    """

    MAX_CHUNK_CHARS: int = 400
    CHUNK_SILENCE_SEC: float = 0.1

    def __init__(self, config: dict):
        self.config = config
        model_cfg = config.get("pocket_tts", {})

        _ensure_pocket_tts_installed()
        from pocket_tts import TTSModel

        # Parametri dal config (con default Pocket TTS)
        language = model_cfg.get("language", "italian_24l")
        temperature = model_cfg.get("temperature", 0.7)
        lsd_decode_steps = model_cfg.get("lsd_decode_steps", 1)
        eos_threshold = model_cfg.get("eos_threshold", -4.0)
        quantize = model_cfg.get("quantize", True)

        self.sr = 24000  # Pocket TTS nativo (24kHz)

        logger.info(
            f"Loading Pocket TTS | language={language} | temp={temperature} | "
            f"steps={lsd_decode_steps} | quantize={quantize}"
        )

        self.model = TTSModel.load_model(
            language=language,
            temp=temperature,
            lsd_decode_steps=lsd_decode_steps,
            eos_threshold=eos_threshold,
            quantize=quantize,
        )

        logger.info(
            f"Pocket TTS loaded! device={self.model.device} | sr={self.sr} Hz | "
            f"max_chunk={self.MAX_CHUNK_CHARS} chars"
        )

        # Cache per voice states (.safetensors sono veloci da caricare)
        self._voice_states_cache: Dict[str, any] = {}

    def _get_voice_state(self, ref: str):
        """Carica o recupera dalla cache il voice state per una reference voice.

        Rileva automaticamente:
          - Nome catalog (es. "giovanni") → NO HF login necessario
          - File path (.wav/.safetensors) → richiede HF login + termini accettati
        """
        if ref not in self._voice_states_cache:
            is_catalog = _is_catalog_voice(ref)
            label = ref if is_catalog else os.path.basename(ref)
            mode_label = "catalog" if is_catalog else "clone"
            logger.info(f"Voice state cache MISS → {label} ({mode_label})")

            try:
                voice_state = self.model.get_state_for_audio_prompt(
                    ref, truncate=False
                )
                self._voice_states_cache[ref] = voice_state
                logger.info(f"Voice state cache: {len(self._voice_states_cache)} voce/i")
            except Exception as e:
                error_msg = str(e).lower()
                if "voice cloning" in error_msg or "clone" in error_msg:
                    logger.error(
                        f"Failed to load voice state for {ref}: {e}\n"
                        f"  → Se usi file personalizzati (.wav), serve il modello con voice cloning:\n"
                        f"    1. Vai su https://huggingface.co/kyutai/pocket-tts e accetta i termini\n"
                        f"    2. Esegui: uvx hf auth login\n\n"
                        f"  → Per usare le voci SENZA HF login, metti un nome catalog in voice_rotation.voices:\n"
                        f"     es: - \"giovanni\"  (IT) | - \"alba\"  (EN) | - \"lola\"  (ES)"
                    )
                else:
                    logger.error(f"Failed to load voice state for {ref}: {e}")
                raise

        else:
            label = ref if _is_catalog_voice(ref) else os.path.basename(ref)
            logger.debug(f"Voice state cache HIT → {label}")

        return self._voice_states_cache[ref]

    def warm_up_cache(self, voice_refs: List[str]):
        """Pre-carica le reference voices come voice states.

        In catalog mode, voice_refs sono nomi voci (es. ["giovanni", "alba"]).
        In clone mode, sono file path (.wav/.safetensors).
        """
        logger.info(f"Warming up Pocket TTS for {len(voice_refs)} voce/i...")
        for ref in voice_refs:
            is_catalog = _is_catalog_voice(ref)
            label = ref if is_catalog else os.path.basename(ref)

            # In clone mode, skip missing files (warned at init time)
            if not is_catalog and not os.path.exists(ref):
                logger.warning(f"Warm-up: file not found → {ref}")
                continue

            try:
                self._get_voice_state(ref)
            except Exception as e:
                logger.warning(f"Warm-up failed for {label}: {e}")
        logger.info("Pocket TTS warm-up completed (voice states ready)")

    def clear_cache(self, ref_audio: Optional[str] = None):
        """Svuota la cache dei voice states."""
        if ref_audio:
            self._voice_states_cache.pop(ref_audio, None)
        else:
            self._voice_states_cache.clear()

    def _split_into_chunks(self, text: str) -> List[str]:
        """Divide il testo in chunk più piccoli."""
        if len(text) <= self.MAX_CHUNK_CHARS:
            return [text.strip()]

        sentences = re.split(r"(?<=[.!?])\s+", text.strip())
        chunks: List[str] = []
        current = ""

        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue

            candidate = f"{current} {sentence}".strip() if current else sentence
            if len(candidate) <= self.MAX_CHUNK_CHARS:
                current = candidate
            else:
                if current:
                    chunks.append(current.strip())
                current = sentence

        if current:
            while len(current) > self.MAX_CHUNK_CHARS:
                split_at = current.rfind(" ", 0, self.MAX_CHUNK_CHARS)
                if split_at == -1:
                    split_at = self.MAX_CHUNK_CHARS
                chunks.append(current[:split_at].strip())
                current = current[split_at:].strip()
            if current:
                chunks.append(current.strip())

        chunks = [c for c in chunks if c]
        logger.debug(f"Text split into {len(chunks)} chunks")
        return chunks

    def generate_realtime_stream(
        self,
        text: str,
        ref_audio: Optional[str] = None,
        ref_text: Optional[str] = None,
        speed: float = 1.0,
        inference_timesteps: Optional[int] = None,
    ) -> Generator[np.ndarray, None, None]:
        """Generatore realtime per playback immediato (24kHz).

        ref_audio può essere:
          - Nome catalog (es. "giovanni") → NO HF login necessario
          - File path (.wav/.safetensors) → richiede HF login + termini accettati
        """
        if not ref_audio:
            ref_audio = self.config.get("model", {}).get("ref_audio_path", "")

        # Validation: catalog voices are names, files must exist
        is_catalog = _is_catalog_voice(ref_audio)
        if not is_catalog and not os.path.exists(ref_audio):
            raise FileNotFoundError(
                f"Reference audio not found: {ref_audio}\n"
                f"  → Il file deve esistere per il voice cloning.\n"
                f"  → Per usare le voci SENZA HF login, metti un nome catalog in voices:\n"
                f"     es: - \"giovanni\" (IT) | - \"alba\" (EN) | - \"lola\" (ES)"
            )

        display_name = ref_audio if is_catalog else os.path.basename(ref_audio)
        voice_state = self._get_voice_state(ref_audio)
        chunks = self._split_into_chunks(text)
        n = len(chunks)
        silence = np.zeros(int(self.CHUNK_SILENCE_SEC * self.sr), dtype=np.float32)

        logger.info(
            f"Streaming Pocket TTS | voice: {display_name} | "
            f"{n} chunk/s | {len(text)} chars"
        )

        for idx, chunk in enumerate(chunks, start=1):
            logger.debug(f"  Chunk {idx}/{n}: '{chunk[:50]}...'")

            try:
                audio_chunks = []
                for piece in self.model.generate_audio_stream(voice_state, chunk):
                    if piece is not None and len(piece) > 0:
                        audio_chunks.append(piece.numpy().astype(np.float32))

                # Yield tutto il chunk come singolo pezzo (Pocket TTS è già veloce su CPU)
                if audio_chunks:
                    combined = np.concatenate(audio_chunks)
                    yield combined.astype(np.float32)
            except Exception as e:
                logger.error(f"Error generating chunk {idx}: {e}")
                raise

            # Silence tra i chunk (non dopo l'ultimo)
            if idx < n:
                yield silence

        logger.info("Pocket TTS streaming completed.")

    def generate_voice_clone(
        self,
        text: str,
        ref_audio: Optional[str] = None,
        ref_text: Optional[str] = None,
        **kwargs,
    ) -> Tuple[np.ndarray, int]:
        """Genera audio completo concatenando lo stream.

        Rileva automaticamente: nome catalog (no HF) o file path (serve HF login).
        """
        if not ref_audio:
            ref_audio = self.config.get("model", {}).get("ref_audio_path", "")

        parts: List[np.ndarray] = []
        for chunk in self.generate_realtime_stream(text, ref_audio=ref_audio):
            parts.append(chunk)

        if not parts:
            return np.zeros(0, dtype=np.float32), self.sr

        full_audio = np.concatenate(parts)
        full_audio = self._post_process(full_audio)
        logger.info(f"Done: {len(full_audio) / self.sr:.1f}s of audio")
        return full_audio, self.sr

    def generate_simple(
        self,
        text: str,
    ) -> Tuple[np.ndarray, int]:
        """Generazione semplice senza streaming."""
        default_ref = self.config.get("model", {}).get("ref_audio_path", "")
        if not default_ref:
            raise ValueError(
                "Pocket TTS requires a reference audio. "
                "Configure model.ref_audio_path in config."
            )
        return self.generate_voice_clone(text, ref_audio=default_ref)

    def _post_process(self, audio: np.ndarray) -> np.ndarray:
        """Trim silenzio e normalizzazione."""
        threshold = 0.005
        mask = np.abs(audio) > threshold
        if mask.any():
            last = int(np.where(mask)[0][-1])
            padding = int(0.3 * self.sr)
            audio = audio[: min(last + padding, len(audio))]

        # Fade-out finale
        fade_len = int(0.1 * self.sr)
        if len(audio) > fade_len:
            audio[-fade_len:] *= np.linspace(1.0, 0.0, fade_len)

        # Normalizzazione
        max_val = np.max(np.abs(audio))
        if max_val > 0:
            audio = audio / max_val * 0.89

        return audio.astype(np.float32)
