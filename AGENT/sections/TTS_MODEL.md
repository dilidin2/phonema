# TTS Model - Data Flow Documentation

**Commit**: `6b87d5c Merge pull request #2 from dilidin2/refactoring`
**Last updated**: 2026-07-05

## Section Overview
VoxCPM2 (openbmb/VoxCPM2) model wrapper with CUDA/CPU support. Handles model loading, voice cloning via reference audio latents, text chunking, and realtime streaming generation.

## Files in this Section
- `models/voxcpm_tts_model.py` — Full pipeline (380 lines)

## Data Flow Map for voxcpm_tts_model.py

### 1. Initialization (`__init__`)
1. **Input**: Config dict with `model.pretrained_path`, `model.dtype`, `model.inference_timesteps`, `model.language`
2. **Transformation**:
   - Detect device: CUDA available → "cuda", else → "cpu"
   - Load VoxCPM from HuggingFace with `load_denoiser=False, optimize=False`
   - Set dtype mapping: float32/float16/bfloat16
3. **Output**: Self-contained pipeline with `model`, `device`, `sr=48000`, `_latents_cache`

### 2. Reference Voice Latent Cache (`_get_latents`)
1. **Input**: Path to reference audio file
2. **Transformation**: Check cache → if miss, compute latents; if hit, return cached dict with `ref_path`
3. **Output**: Dict `{"ref_path": path}` — reused across all chunks for same voice

### 3. Voice Rotation (`warm_up_cache`)
1. **Input**: List of voice file paths
2. **Transformation**: Pre-compute latents for each file (no inference, just conditioning)
3. **Output**: All voices ready in `_latents_cache` — faster subsequent generation

### 4. Text Chunking (`_split_into_chunks`)
1. **Input**: Full text string
2. **Transformation**:
   - If ≤400 chars → single chunk
   - Else: split by sentences at `.!?`, then by whitespace, respecting 400-char limit
3. **Output**: List of string chunks

### 5. Streaming Inference (`generate_realtime_stream`)
1. **Input**: Text + ref_audio path + optional inference_timesteps
2. **Transformation**:
   - Get latents from cache for ref_audio
   - Split text into chunks via `_split_into_chunks`
   - For each chunk: call `model.generate_streaming()` → yield audio pieces as numpy arrays at 48kHz
   - Yield silence between chunks (0.1s of zeros)
3. **Output**: Generator yielding `np.ndarray` chunks in real-time order

## Dependencies
- External: `voxcpm`, `torch` (with CUDA or CPU fallback), `numpy`, `soundfile`, `loguru`
- Internal: Called from `services/tts_service.py` via `TTSService._worker_loop()`
- Config: `config/tts_config.yaml` model section (pretrained_path, dtype, inference_timesteps, language)
