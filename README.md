# Phonema

<p align="center">
  <img src="img/image.png" width="480">
</p>

> *Phoneme* — the smallest unit of sound in a spoken language.

Real-time Text-to-Speech service for Twitch. Listens to channel point redemptions and speaks the user's message instantly via audio streaming.

Supports two TTS backends:
- **VoxCPM2** — high-quality GPU model (requires NVIDIA/AMD GPU)
- **Pocket TTS** — lightweight CPU model by Kyutai, no GPU needed

## Features

- **Dual model support** — Choose between VoxCPM2 (GPU, high quality) or Pocket TTS (CPU, lightweight)
- **Real-time streaming** — Audio plays while the model is still generating (no waiting)
- **Voice cloning** — Reference audio determines voice characteristics
- **Catalog voices** — Pocket TTS includes 20+ built-in voices across EN/IT/ES/DE/PT/FR (no HF login needed)
- **Voice rotation** — Cycle through multiple voices (random or sequential mode), mixing catalog names and custom files
- **Queue management** — Back-pressure controlled concurrent request handling
- **Auto-reconnect** — OAuth tokens persisted to `token.json` for seamless resumption
- **Cross-platform** — CUDA (NVIDIA), ROCm (AMD), and CPU-only supported

## Architecture

```
Twitch EventSub ──► TwitchService ──► TTS Queue ──► TTS Worker
                                                          │
                                              ┌───────────┴───────────┐
                                              │                       │
                                    VoxCPM2 (GPU)            Pocket TTS (CPU)
                                              │                       │
                                          Audio Buffer              Audio Buffer
                                              │                       │
                                          sounddevice ─────► Speakers ◄─────────────┘
```

One worker processes inference sequentially. Producer/consumer pattern streams chunks to the audio output with back-pressure control.

### Model Selection

Set `model_type` in `config/tts_config.yaml`:
- **`"gpu_model"`** — VoxCPM2 (openbmb/VoxCPM2). Requires CUDA/ROCm GPU. 48kHz, bfloat16, highest quality.
- **`"cpu_model"`** — Pocket TTS (Kyutai). Runs on CPU. 24kHz, int8 quantized, lightweight (~300MB vs ~5GB).

The service auto-detects the model at startup and adjusts sample rate accordingly.

## Installation

### Clone the repo and initialize the environment

If you don't have `uv` installed yet, follow [the official installation guide](https://docs.astral.sh/uv/getting-started/installation/).

```bash
git clone https://github.com/dilidin2/phonema-twitch-tts.git
cd phonema-twitch-tts
uv venv
source .venv/bin/activate # Windows: .venv\Scripts\activate
```

### 1. PyTorch

Pick the build matching your hardware:

**NVIDIA GPU (CUDA 12.4–12.6) — for VoxCPM2:**
```bash
uv pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu126
```

**AMD GPU (ROCm 7.2) — for VoxCPM2:**
```bash
uv pip install torch torchaudio --index-url https://download.pytorch.org/whl/rocm7.2
```

**CPU only — for Pocket TTS:**
```bash
uv pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
```

> Pocket TTS runs on CPU and works with the standard PyTorch CPU build.

### 2. Project dependencies

```bash
uv pip install -r requirements.txt
# or manually:
pip install voxcpm numpy fastapi uvicorn python-multipart twitchAPI pyyaml \
           python-dotenv loguru sounddevice soundfile aiohttp
```

**For Pocket TTS (CPU mode):** Uncomment `pocket-tts` and `scipy` in `requirements.txt`, then install:
```bash
pip install pocket-tts scipy
```

## Configuration

### Environment Variables (`.env`)

Copy `.env.example` to `.env`

```bash
cp .env.example .env # Windows: rename .env.example to .env manually
```

and fill in the values:

```ini
# Bot account username (use your channel name if no separate bot account)
TWITCH_BOT_USERNAME=your_bot_username

# Numeric broadcaster ID (your channel's user ID, not username)
# Get it: https://www.streamweasels.com/tools/convert-twitch-username-to-user-id/
TWITCH_BROADCASTER_ID=123456789
```

> **No Twitch app registration needed.** The bot uses a public client via the Device Code Flow — just start it and authorize from your browser.

### Model Config (`config/tts_config.yaml`)

Change the name of the config:

```bash
cp config/tts_config.yaml.example config/tts_config.yaml # Windows: rename the file manually
```

Full config reference:

```yaml
# ── Model selection ────────────────────────────────────────────────
# "gpu_model"  → VoxCPM2 (heavy, requires CUDA/ROCm GPU)
# "cpu_model"  → Pocket TTS (lightweight, runs on CPU)
model_type: "gpu_model"

# ── VoxCPM2 config (only used when model_type = "gpu_model") ───────
model:
  pretrained_path: "openbmb/VoxCPM2" # HuggingFace model ID
  dtype: "bfloat16"
  inference_timesteps: 5
  # VoxCPM2 native sample rate
  sr: 48000

  ref_audio_path: "config/reference_voice.wav" # Fallback voice file

# ── Pocket TTS config (only used when model_type = "cpu_model") ────
pocket_tts:
  language: "italian_24l"       # "english", "italian_24l", "french_24l", "german_24l", "portuguese_24l", "spanish_24l"
  temperature: 0.7              # Lower = more deterministic
  lsd_decode_steps: 1           # More steps = higher quality but slower (try 5 for HQ)
  eos_threshold: -4.0           # End-of-sequence threshold
  quantize: true                # int8 quantization: less RAM, faster, minimal quality loss
```

### Voice Rotation Config

```yaml
voice_rotation:
  mode: "random"          # "random", "sequential", or "disabled"
  voices_dir: "config/voices"
  voices:
    # Mix catalog voices (no HF login) and custom files (HF login required):
    - "giovanni"           # Built-in Italian voice (catalog)
    - "alba"               # Built-in English voice (catalog)
    - "voice_a.wav"        # Custom .wav in voices_dir (clone mode)
```

**Catalog voices** — Pocket TTS built-in voices that work without HF login:

| Language | Voices |
|----------|--------|
| **EN** | `cosette`, `marius`, `javert`, `alba`, `jean`, `anna`, `vera`, `fantine`, `charles`, `paul`, `eponine`, `azelma`, `george`, `mary`, `jane`, `michael`, `eve`, `bill_boerst`, `peter_yearsley`, `stuart_bell`, `caro_davy` |
| **IT** | `giovanni` |
| **ES** | `lola` |
| **DE** | `juergen` |
| **PT** | `rafael` |
| **FR** | `estelle` |

> Custom voice files (`.wav` / `.safetensors`) require accepting terms on [HuggingFace](https://huggingface.co/kyutai/pocket-tts) and logging in with `uvx hf auth login`.

### Pocket TTS Languages

| Config value | Language |
|---|---|
| `english` | English (default voice: `alba`) |
| `italian_24l` | Italian |
| `french_24l` | French |
| `german_24l` | German |
| `portuguese_24l` | Portuguese |
| `spanish_24l` | Spanish |

### Setup Voice Files

**For VoxCPM2 (GPU):**
1. Place at least one `.wav` file in the root `config/` directory named `reference_voice.wav`
2. For voice rotation, add additional `.wav` files to `config/voices/`
3. Reference audio should be 5-30 seconds of clear speech for best results

**For Pocket TTS (CPU):**
- **Catalog mode** — No files needed! Just use built-in voice names in `voice_rotation.voices` (e.g., `"giovanni"`, `"alba"`). Works without HF login.
- **Clone mode** — Place `.wav` or `.safetensors` files in `config/voices/`. Requires HF login + accepting terms on [kyutai/pocket-tts](https://huggingface.co/kyutai/pocket-tts).

## Usage

### Start the server

```bash
python main.py
```

The server starts on port 8100 by default. Open Swagger docs at `http://localhost:8100/docs`.

At startup, the log shows which model is loaded:
```
Model: VoxCPM2 (GPU, BMB/ModelScope)     ← if model_type = "gpu_model"
Model: Pocket TTS (CPU, Kyutai)           ← if model_type = "cpu_model"
```

### Connect to Twitch

First-time connection uses the Device Code Flow:

```bash
curl -X POST http://localhost:8100/twitch/auth/start
```

This returns a `user_code` and `verification_uri`. Open the URI in your browser, enter the code, and authorize. Tokens are saved to `token.json` and reused on restart.

You can check auth progress with:

```bash
curl http://localhost:8100/twitch/auth/status
```

For auto-connect on startup, set `TWITCH_BROADCASTER_ID` in `.env` — the service attempts connection on launch using saved tokens.

### API Endpoints

**TTS:**
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/tts/speak` | Generate speech from text |
| GET | `/tts/status` | Check queue status |

```bash
# Speak a message
curl -X POST http://localhost:8100/tts/speak \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello from Twitch TTS!", "voice_id": null}'
```

**Twitch:**
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/twitch/auth/start` | Start Device Code Flow authorization |
| GET | `/twitch/auth/status` | Check DCF auth progress |
| POST | `/twitch/connect` | Connect using saved tokens (if any) |
| POST | `/twitch/reconnect` | Reconnect using saved tokens |
| POST | `/twitch/disconnect` | Disconnect EventSub |
| GET | `/twitch/status` | Connection status |

**Health:**
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Model type, CUDA status (if GPU), queue size, worker count |

The `/health` endpoint reports `model_type` (`gpu_model` / `cpu_model`). For CPU mode, `cuda_available: false` is normal and the status stays `ok`.

## Model Comparison

| Feature | VoxCPM2 (GPU) | Pocket TTS (CPU) |
|---------|--------------|------------------|
| **Quality** | Very high | Good |
| **Hardware** | CUDA / ROCm GPU | Any CPU |
| **Model size** | ~5 GB | ~300 MB |
| **Sample rate** | 48 kHz | 24 kHz |
| **Voice cloning** | Yes (custom .wav) | Yes (catalog + custom .wav) |
| **Catalog voices** | No | 20+ built-in voices |
| **HF login required** | No | Only for custom clone files |
| **Languages** | Configured per model | EN, IT, FR, DE, PT, ES |

## License

MIT License.

VoxCPM2 model is licensed under Apache 2.0 (by OpenBMB). Respect their license when using model weights.
Pocket TTS model by Kyutai — check [kyutai/pocket-tts](https://huggingface.co/kyutai/pocket-tts) for licensing details.
