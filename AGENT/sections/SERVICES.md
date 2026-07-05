# Services - Data Flow Documentation

**Commit**: `6b87d5c Merge pull request #2 from dilidin2/refactoring`
**Last updated**: 2026-07-05

## Section Overview
Three async services: TTSService (queue-based worker pool + VoiceRotator), AudioOutputService (sounddevice streaming playback with back-pressure), and TwitchService (EventSub WebSocket for Channel Points redemptions + OAuth token management).

## Files in this Section
- `services/tts_service.py` — Queue workers, voice rotation, request submission
- `services/audio_output.py` — sounddevice stream playback, chunk routing
- `services/twitch_service.py` — EventSub WebSocket, OAuth auth, redemption listener

## Data Flow Map for tts_service.py

### VoiceRotator
1. **Input**: Config dict with `voice_rotation.mode` (sequential/random/disabled) and voice file paths
2. **Transformation**:
   - Build list of voice files from config or fall back to single ref_audio_path
   - Validate all files exist at runtime
   - Track index for sequential, last-chosen for random mode
3. **Output**: Selected voice path per `next()` call

### TTSService (worker pool)
1. **Input**: Config dict + AudioOutputService instance
2. **Startup**: Create asyncio.Queue(maxsize=10), spawn N worker tasks with `_is_running=True`
3. **Request flow** (`submit_request`):
   - `queue.put(request)` with 5s timeout
   - Returns True on success, False if queue full
4. **Worker loop** (`_worker_loop`):
   - Dequeue request → validate text exists
   - Lazy-load VoxCPM2 model on first start (under `_inference_lock`)
   - Select ref_audio via VoiceRotator
   - Spawn producer+consumer pair:
     - **producer**: `VoxCPM2.generate_realtime_stream()` in ONNX thread → put chunks into `audio_buffer` (maxsize=100, back-pressure)
     - **consumer**: Read from audio_buffer → accumulate chunks → flush to `play_chunk_sync()` when buffer full or streaming done
   - Shutdown: sentinel (`None`) sent to each worker's queue

## Data Flow Map for audio_output.py

### AudioOutputService
1. **Input**: Method string ("direct" or "streamerbot")
2. **Initialization**:
   - Create sounddevice.OutputStream(samplerate=48000, channels=1, blocksize=2048, latency="low")
   - 2048 samples ≈ 85ms ring-buffer — key for natural back-pressure
3. **Playback** (`play_chunk_sync`):
   - Cast chunk to float32 → `stream.write()` (blocks when ring-buffer full)
   - If stream inactive → attempt restart
4. **File playback** (`play`): Read WAV via soundfile, call `play_chunk_sync()` on data

## Data Flow Map for twitch_service.py

### TwitchService
1. **Input**: Config dict with TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET
2. **Connection flow** (`connect`):
   - Create Twitch client (twitchAPI v4)
   - Register `_user_auth_refresh_callback` → auto-save tokens to `token.json`
   - Load existing tokens from `token.json` OR open browser for OAuth
   - Set user authentication with scopes: `[CHANNEL_READ_REDEMPTIONS]`
3. **EventSub** (`authenticate_user`): Create EventSubWebsocket client
4. **Redemption listener** (`listen_channel_points_redemption`):
   - Subscribe to `channel_points_custom_reward_redemption_add` for broadcaster_id
   - Callback formats: `{user_input, user_id, user_name, reward_title}`
   - Calls `on_redemption` if set (wired to TTSService in main.py)
5. **Reconnection**: Retry with exponential backoff (2^attempt seconds), max 5 retries

### Token Management
- Tokens saved: `{token, refresh}` → `token.json`
- Refresh callback auto-saves on every OAuth token refresh (~4h lifespan)
- Auto-reauth triggers on 401 errors during EventSub subscribe

## Dependencies
- External: `twitchAPI>=4.5.0`, `sounddevice`, `numpy`, `loguru`
- Internal: TTSService ↔ TwitchService (via `on_redemption` callback in main.py)
- Shared state: AudioOutputService injected into TTSService for chunk playback
