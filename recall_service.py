import asyncio
import logging

import httpx

from . import config

logger = logging.getLogger(__name__)


def _headers() -> dict:
    return {"Authorization": f"Token {config.RECALL_API_KEY}"}


async def send_bot_to_meeting(meeting_url: str) -> dict:
    body = {
        "meeting_url": meeting_url,
        "bot_name": config.RECALL_BOT_NAME,
        "recording_config": {"video_mixed_mp4": {}},
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{config.RECALL_BASE_URL}/bot/",
            headers=_headers(),
            json=body,
        )
        resp.raise_for_status()
        return resp.json()


async def get_bot_status(bot_id: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{config.RECALL_BASE_URL}/bot/{bot_id}/",
            headers=_headers(),
        )
        resp.raise_for_status()
        return resp.json()


async def delete_bot(bot_id: str) -> None:
    async with httpx.AsyncClient() as client:
        resp = await client.delete(
            f"{config.RECALL_BASE_URL}/bot/{bot_id}/",
            headers=_headers(),
        )
        resp.raise_for_status()


async def list_bots(cursor: str | None = None) -> dict:
    params = {}
    if cursor:
        params["cursor"] = cursor
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{config.RECALL_BASE_URL}/bot/",
            headers=_headers(),
            params=params,
        )
        resp.raise_for_status()
        return resp.json()


async def cleanup_old_bots(max_age_days: int = 180) -> int:
    """Delete bots older than max_age_days. Returns number of deleted bots."""
    from datetime import datetime, timezone, timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    deleted = 0
    cursor = None

    while True:
        data = await list_bots(cursor=cursor)
        bots = data.get("results", [])

        for bot in bots:
            created_at = bot.get("created_at", "")
            if not created_at:
                continue
            try:
                bot_time = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            except Exception:
                continue

            if bot_time < cutoff:
                bot_id = bot["id"]
                try:
                    await delete_bot(bot_id)
                    deleted += 1
                    logger.info(f"Deleted old bot {bot_id} (created {created_at})")
                except Exception:
                    logger.exception(f"Failed to delete bot {bot_id}")

        cursor = data.get("next")
        if not cursor:
            break

    logger.info(f"Cleanup complete: deleted {deleted} bot(s) older than {max_age_days} days")
    return deleted


async def wait_for_recording(bot_id: str) -> dict:
    timeout = config.POLL_TIMEOUT_HOURS * 3600
    elapsed = 0

    # Statuses that mean the bot is still active and we should keep polling
    _ACTIVE_STATUSES = {
        "ready",
        "joining_call",
        "in_waiting_room",
        "in_call_not_recording",
        "in_call_recording",
        "recording_permission_allowed",
        "recording_permission_denied",
    }

    while elapsed < timeout:
        try:
            bot_data = await get_bot_status(bot_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise RuntimeError(f"Bot {bot_id} no longer exists (404 from Recall API)")
            raise

        # Check if recording is ready
        recordings = bot_data.get("recordings", [])
        if recordings:
            download_url = (
                recordings[0]
                .get("media_shortcuts", {})
                .get("video_mixed", {})
                .get("data", {})
                .get("download_url")
            )
            if download_url:
                logger.info(f"Recording ready for bot {bot_id}")
                return bot_data

        # Check if bot failed or left
        status_obj = bot_data.get("status", {})
        status = status_obj.get("code", "")
        sub_code = status_obj.get("sub_code", "")

        if status in ("fatal", "done", "analysis_done"):
            if not recordings:
                raise RuntimeError(f"Bot {bot_id} finished with status '{status}' but no recording")

        # Bot never joined or was rejected
        if status in ("call_ended", "fatal") or sub_code in (
            "cannot_join_meeting",
            "meeting_not_found",
            "bot_kicked",
        ):
            raise RuntimeError(f"Bot {bot_id} cannot join: status={status}, sub_code={sub_code}")

        # Unknown / unexpected status — stop polling instead of waiting 4 hours
        if status and status not in _ACTIVE_STATUSES:
            raise RuntimeError(f"Bot {bot_id} reached unexpected terminal status: {status} (sub: {sub_code})")

        logger.info(f"Bot {bot_id} status: {status} (sub: {sub_code}), waiting {config.POLL_INTERVAL_SECONDS}s...")
        await asyncio.sleep(config.POLL_INTERVAL_SECONDS)
        elapsed += config.POLL_INTERVAL_SECONDS

    raise TimeoutError(f"Recording not ready after {config.POLL_TIMEOUT_HOURS}h for bot {bot_id}")
