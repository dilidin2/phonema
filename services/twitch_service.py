"""
Twitch Service - WebSocket integration with Twitch API
Handles real-time Channel Points redemption events using EventSub
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Callable, Optional

from aiohttp import ClientSession
from loguru import logger

# Import twitchAPI v4 components
try:
    from twitchAPI.eventsub.websocket import EventSubWebsocket
    from twitchAPI.twitch import Twitch
    from twitchAPI.type import AuthScope
except ImportError:
    raise ImportError("Install twitchAPI: pip install 'twitchAPI>=4.5.0'")

TOKEN_PATH = Path("token.json")
REQUIRED_SCOPES = [AuthScope.CHANNEL_READ_REDEMPTIONS]

DEVICE_CODE_URL = "https://id.twitch.tv/oauth2/device"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"


def _save_tokens(token: str, refresh_token: str) -> None:
    with open(TOKEN_PATH, "w") as f:
        json.dump({"token": token, "refresh": refresh_token}, f)
    logger.debug("Tokens saved to token.json")


class TwitchService:
    """Manages EventSub connection to Twitch for real-time events"""

    def __init__(self, config: dict):
        self.config = config
        self.client_id = config.get("TWITCH_CLIENT_ID", "")

        self.twitch: Optional[Twitch] = None
        self.eventsub: Optional[EventSubWebsocket] = None
        self.on_redemption: Optional[Callable] = None
        self._broadcaster_id: Optional[str] = None

        # DCF state
        self._auth_status: str = "idle"  # idle | pending | success | expired | denied
        self._auth_future: Optional[asyncio.Future] = None

    # ── Auth helpers ──────────────────────────────────────────────────────────

    async def _user_auth_refresh_callback(self, token: str, refresh_token: str) -> None:
        """
        Called automatically by twitchAPI whenever the OAuth token is refreshed.
        Persisting new tokens here prevents silent expiry after ~4 hours.
        """
        logger.info("OAuth token refreshed — saving new tokens to token.json")
        _save_tokens(token, refresh_token)

    async def _get_device_code(self, scopes: list) -> dict:
        """Request a device code from Twitch."""
        scope_str = " ".join(s.value if hasattr(s, "value") else str(s) for s in scopes)
        data = {"client_id": self.client_id, "scope": scope_str}
        async with ClientSession() as session:
            async with session.post(DEVICE_CODE_URL, data=data) as resp:
                return await resp.json()

    async def _poll_for_token(
        self, device_code: str, interval: int, scopes: list
    ) -> tuple[str, str]:
        """
        Poll Twitch for the user token after they authorize.
        Raises TimeoutError on expired code, RuntimeError on denial.
        """
        current_interval = interval
        scope_str = " ".join(s.value if hasattr(s, "value") else str(s) for s in scopes)
        async with ClientSession() as session:
            while True:
                data = {
                    "client_id": self.client_id,
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "scope": scope_str,
                }
                async with session.post(TOKEN_URL, data=data) as resp:
                    result = await resp.json()

                status = result.get("status", 200)
                if status == 200:
                    return result["access_token"], result["refresh_token"]

                error = result.get("error")
                if error == "authorization_pending":
                    pass  # keep polling
                elif error == "slow_down":
                    logger.warning("Twitch OAuth: slow_down — increasing poll interval")
                    current_interval += 5
                elif error == "expired_token":
                    self._auth_status = "expired"
                    if self._auth_future and not self._auth_future.done():
                        self._auth_future.set_exception(
                            TimeoutError("Authorization expired")
                        )
                    raise TimeoutError(
                        "Device code expired — user did not authorize in time"
                    )
                elif error == "access_denied":
                    self._auth_status = "denied"
                    if self._auth_future and not self._auth_future.done():
                        self._auth_future.set_exception(
                            RuntimeError("User denied authorization")
                        )
                    raise RuntimeError("User denied authorization")

                await asyncio.sleep(current_interval)

    async def _do_device_auth(self) -> tuple[str, str]:
        """
        Execute the Device Code Flow (blocking).
        Returns (access_token, refresh_token).
        """
        device_info = await self._get_device_code(REQUIRED_SCOPES)
        user_code = device_info["user_code"]
        verification_uri = device_info["verification_uri"]
        expires_in = device_info["expires_in"]
        interval = device_info["interval"]

        logger.info(
            f"Twitch Device Code Flow — go to {verification_uri} "
            f"and enter code: {user_code}"
        )
        logger.info(f"Code expires in {expires_in}s, polling every {interval}s")

        self._auth_status = "pending"

        token, refresh_token = await self._poll_for_token(
            device_info["device_code"], interval, REQUIRED_SCOPES
        )

        self._auth_status = "success"
        return token, refresh_token

    async def _start_device_flow_async(self) -> dict:
        """
        Start DCF in the background. Returns info for the user to complete auth.
        On success the service auto-connects (connect + authenticate_user + listen).
        """
        device_info = await self._get_device_code(REQUIRED_SCOPES)
        user_code = device_info["user_code"]
        verification_uri = device_info["verification_uri"]
        expires_in = device_info["expires_in"]
        interval = device_info["interval"]

        logger.info(
            f"Twitch Device Code Flow — go to {verification_uri} "
            f"and enter code: {user_code}"
        )
        logger.info(f"Code expires in {expires_in}s, polling every {interval}s")

        self._auth_status = "pending"
        self._auth_future = asyncio.get_event_loop().create_future()

        # Start polling + auto-connect in background
        asyncio.create_task(
            self._poll_and_connect(device_info["device_code"], interval)
        )

        return {
            "verification_uri": verification_uri,
            "user_code": user_code,
            "expires_in": expires_in,
        }

    async def _poll_and_connect(self, device_code: str, interval: int) -> None:
        """
        Poll for token and, on success, connect to Twitch automatically.
        """
        try:
            token, refresh_token = await self._poll_for_token(
                device_code, interval, REQUIRED_SCOPES
            )
            _save_tokens(token, refresh_token)

            # Connect with the new tokens
            await self.connect()
            await self.authenticate_user()

            broadcaster_id = os.getenv("TWITCH_BROADCASTER_ID")
            if broadcaster_id:
                await self.listen_channel_points_redemption(broadcaster_id)
                logger.info("✓ Twitch connected automatically after DCF auth")

            if self._auth_future and not self._auth_future.done():
                self._auth_future.set_result(True)

        except Exception as e:
            logger.error(f"DCF flow failed: {e}")
            if self._auth_future and not self._auth_future.done():
                self._auth_future.set_exception(e)

    # ── Public API ────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """
        Initialize the Twitch client and load tokens from token.json if present.
        Does NOT block on missing tokens — call /twitch/auth/start to authorize.
        """
        logger.info("Initializing Twitch client...")
        self.twitch = Twitch(
            app_id=self.client_id, app_secret="", authenticate_app=False
        )

        # Register the refresh callback BEFORE setting any token, so the
        # library can transparently persist renewed tokens on every refresh.
        self.twitch.user_auth_refresh_callback = self._user_auth_refresh_callback

        if TOKEN_PATH.exists():
            try:
                with open(TOKEN_PATH, "r") as f:
                    creds = json.load(f)
                logger.info("Loading existing tokens from token.json...")
                await self.twitch.set_user_authentication(
                    creds["token"], REQUIRED_SCOPES, creds["refresh"]
                )
                logger.info("✓ Tokens loaded from token.json")
            except Exception as e:
                logger.warning(
                    f"Failed to load tokens ({e}) — starting Device Code Flow for re-authentication"
                )
                token, refresh_token = await self._do_device_auth()
                await self.twitch.set_user_authentication(
                    token, REQUIRED_SCOPES, refresh_token
                )
                _save_tokens(token, refresh_token)
        else:
            logger.info(
                "No token.json found — start Device Code Flow via POST /twitch/auth/start"
            )

        logger.info("Twitch client initialized")

    async def reauthenticate_if_needed(self) -> None:
        """Re-authenticate via Device Code Flow (e.g. after a 401)."""
        logger.warning("Re-authenticating via Device Code Flow...")
        token, refresh_token = await self._do_device_auth()
        await self.twitch.set_user_authentication(token, REQUIRED_SCOPES, refresh_token)
        _save_tokens(token, refresh_token)
        logger.info("✓ Re-authenticated successfully")

    async def authenticate_user(self) -> None:
        """Create the EventSub WebSocket client (call after connect())."""
        logger.info("Creating EventSub client...")
        self.eventsub = EventSubWebsocket(twitch=self.twitch)
        logger.info("EventSub client created")

    async def listen_channel_points_redemption(self, broadcaster_id: str) -> None:
        if not self.eventsub:
            raise RuntimeError("Call authenticate_user() first to create EventSub")

        self._broadcaster_id = broadcaster_id  # Store for reconnect

        # Define the callback BEFORE starting, so it's always in scope.
        async def redemption_callback(data):
            await self._handle_redemption(data)

        async def _subscribe() -> None:
            await self.eventsub.listen_channel_points_custom_reward_redemption_add(
                broadcaster_user_id=broadcaster_id,
                callback=redemption_callback,
            )

        try:
            logger.info(f"Starting EventSub listener for broadcaster: {broadcaster_id}")
            self.eventsub.start()
            await _subscribe()
            logger.info("✓ Listening for channel point redemptions via EventSub")

        except Exception as e:
            error_str = str(e)
            if any(
                k in error_str
                for k in ("401", "Unauthorized", "needs user authentication")
            ):
                logger.warning(
                    "Auth error during EventSub subscribe — re-authenticating..."
                )
                await self.reauthenticate_if_needed()
                # Re-create EventSub with fresh auth state
                self.eventsub = EventSubWebsocket(twitch=self.twitch)
                self.eventsub.start()
                await _subscribe()
                logger.info("✓ Re-subscribed after re-authentication")
            else:
                logger.error(f"Failed to start redemption listener: {e}")
                raise

    # ── Internal handlers ─────────────────────────────────────────────────────

    async def _handle_redemption(self, data) -> None:
        try:
            user_input = data.event.user_input
            user_id = data.event.user_id
            user_name = data.event.user_name or "Someone"
            reward_title = data.event.reward.title

            logger.info(f"💰 Redemption from {user_name} ({user_id}): '{user_input}'")

            if self.on_redemption:
                await self.on_redemption(
                    {
                        "user_input": user_input,
                        "user_id": user_id,
                        "user_name": user_name,
                        "reward_title": reward_title,
                    }
                )

        except Exception as e:
            logger.error(f"Redemption handler failed (user {user_name}): {e}", exc_info=True)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def disconnect(self) -> None:
        if self.eventsub:
            logger.info("Stopping EventSub client...")
            await self.eventsub.stop()
            self.eventsub = None
            logger.info("EventSub disconnected")

    async def reconnect(self, max_retries: int = 5) -> bool:
        if self.eventsub:
            return True  # Already connected

        for attempt in range(max_retries):
            try:
                await self.connect()
                await self.authenticate_user()
                if self._broadcaster_id:
                    await self.listen_channel_points_redemption(self._broadcaster_id)
                return True
            except Exception as e:
                wait_time = 2**attempt
                logger.warning(
                    f"Reconnection attempt {attempt + 1}/{max_retries} failed: {e}. "
                    f"Retrying in {wait_time}s..."
                )
                await asyncio.sleep(wait_time)

        logger.error("Max reconnection attempts reached!")
        return False
