# API - Data Flow Documentation

**Commit**: `6b87d5c Merge pull request #2 from dilidin2/refactoring`
**Last updated**: 2026-07-05

## Section Overview
FastAPI application with lifespan management, CORS middleware, and HTTP endpoints for TTS generation, Twitch connection management, and health monitoring. All state injected via `app.state` during startup.

## Files in this Section
- `main.py` — App entry point + all HTTP endpoints (190 lines)

## Data Flow Map for main.py

### 1. Startup (lifespan)
1. **Input**: Environment variables (`.env`), YAML config (`config/tts_config.yaml`)
2. **Transformation**:
   - Load config: merge YAML + Twitch env vars
   - Check CUDA availability via `torch.cuda.is_available()` (unless GPU disabled)
   - Initialize services: AudioOutputService → TTSService → TwitchService
   - Auto-connect Twitch via saved `token.json` or browser OAuth
   - Register `on_redemption` callback from TwitchService → TTSService.submit_request()
3. **Output**: Services stored on `app.state` for endpoint access

### 2. HTTP Endpoints

#### GET /health
- **Input**: None (read-only)
- **Flow**: Read `app.state.cuda_available`, queue size, worker count
- **Output**: `HealthResponse` with status/cuda/queue/workers

#### POST /tts/speak
- **Input**: JSON `{ "text": "...", "voice_id": "optional" }` (max 500 chars)
- **Flow**: Build request dict → `tts_service.submit_request()` → queue.put() with 5s timeout
- **Output**: `TTSResponse(success=True)` — audio plays asynchronously
- **Error**: Queue full → HTTP 503; service not initialized → HTTP 500

#### GET /tts/status
- **Input**: None (read-only)
- **Flow**: Read queue qsize, maxsize, active workers, is_running flag
- **Output**: Dict with queue status + model info "VoxCPM2"

#### POST /twitch/connect
- **Input**: None
- **Flow**: connect() → authenticate_user() → listen_channel_points_redemption(broadcaster_id)
- **Output**: `{"status": "connected"}`

#### POST /twitch/disconnect
- **Input**: None
- **Flow**: eventsub.stop()
- **Output**: `{"status": "disconnected"}`

#### POST /twitch/reconnect
- **Input**: None
- **Flow**: connect() → authenticate_user() → listen_channel_points_redemption() using saved tokens
- **Output**: `{"status": "reconnected"}`

#### GET /twitch/status
- **Input**: None (read-only)
- **Flow**: Check eventsub instance and `_running` flag
- **Output**: Dict with initialized/connected status

### 3. Shutdown (lifespan teardown)
1. Stop TTS workers → stop audio stream → disconnect EventSub
2. Cleanup order: TTSService → AudioOutputService → TwitchService

## Dependencies
- External: `fastapi`, `uvicorn`, `twitchAPI>=4.5.0`, `voxcpm`, `sounddevice`
- Internal: All three services from `services/` package
- Config: `config/tts_config.yaml` (model, queue, voice_rotation, host/port)
