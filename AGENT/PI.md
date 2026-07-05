# Project Index - Phonema (Twitch Channel Points TTS Server)

## Commit Reference
- Last updated: `6b87d5c Merge pull request #2 from dilidin2/refactoring`
- Current branch: `master`

## Repository Tree
```
phonema/
├── AGENT/                           # Project index (this file + sections/)
│   ├── PI.md                       # ← Root index
│   └── sections/
│       ├── API.md                  # FastAPI endpoints, routes, middleware
│       ├── TTS_MODEL.md            # VoxCPM2 model wrapper, inference
│       └── SERVICES.md             # Audio output, queue workers, Twitch EventSub
├── main.py                         # App entry point + all HTTP endpoints
├── models/
│   └── voxcpm_tts_model.py         # VoxCPM2 pipeline (CPU/GPU), streaming generator
├── services/
│   ├── __init__.py
│   ├── tts_service.py              # Queue-based TTS workers + VoiceRotator
│   ├── audio_output.py             # sounddevice streaming playback
│   └── twitch_service.py           # EventSub WebSocket, OAuth auth, redemption listener
├── config/
│   ├── tts_config.yaml.example     # Model, queue, voice rotation config
│   ├── voices/put_reference_voices_here_.txt  # Voice file list
│   └── put_reference_voice_here_.txt          # Placeholder for reference audio
├── .env.example                    # Twitch credentials + broadcaster ID
├── requirements.txt                # Python dependencies
├── README.md                       # Project documentation
└── img/image.png                   # Project icon
```

## Section Navigation

| Section | Description | Files |
|---------|-------------|-------|
| API | FastAPI endpoints, HTTP routes, middleware | `main.py` |
| TTS_MODEL | VoxCPM2 model loading, streaming inference, voice cloning | `models/voxcpm_tts_model.py` |
| SERVICES | Audio playback, queue workers, Twitch EventSub integration | `services/tts_service.py`, `services/audio_output.py`, `services/twitch_service.py` |

## Data Flow Map (End-to-End)

```
User redemption on Twitch
  │
  ▼
TwitchService._handle_redemption() ──► formats user_input + user_name
  │
  ▼
on_redemption callback ──► TTSService.submit_request()
  │
  ▼
asyncio.Queue (maxsize=10, timeout=5s)
  │
  ├─► Worker N: producer() → VoxCPM2.generate_realtime_stream() → audio_buffer
  │   (ONNX thread via asyncio.to_thread)
  │
  └─► Worker N: consumer() → accumulates chunks → play_chunk_sync()
      (sounddevice stream.write(), back-pressure when ring-buffer full)
  │
  ▼
AudioOutputService.play_chunk_sync() → sounddevice.OutputStream → speaker
```

## Glossary

- **Entry Point**: `main.py` — FastAPI app with lifespan, all HTTP endpoints
- **Config**: `config/tts_config.yaml` + `.env` — Model params, Twitch creds, queue settings
- **TTS Engine**: VoxCPM2 (openbmb/VoxCPM2) — Voice cloning TTS model, 48kHz native
- **VoiceRotator**: Cycles/random/disabled voice selection for reference audio rotation
- **EventSub**: Twitch WebSocket subscription for Channel Points redemptions
- **Queue**: asyncio.Queue(maxsize=10) with back-pressure via sounddevice latency='low'
