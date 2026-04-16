import asyncio
import logging
from datetime import datetime, timezone

from supabase import Client, create_client
import os

from . import config

logger = logging.getLogger(__name__)

TRACKING_TABLE = "meeting_bot_upa"


def _get_supabase() -> Client:
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def get_tracking_record(event_id: str) -> dict | None:
    client = _get_supabase()
    rows = (
        client.table(TRACKING_TABLE)
        .select("*")
        .eq("outlook_event_id", event_id)
        .execute()
        .data
    )
    return rows[0] if rows else None


def create_tracking_record(
    event_id: str,
    subject: str,
    meeting_url: str,
    meeting_start: str,
) -> dict:
    client = _get_supabase()
    return (
        client.table(TRACKING_TABLE)
        .insert({
            "outlook_event_id": event_id,
            "meeting_subject": subject,
            "meeting_url": meeting_url,
            "meeting_start": meeting_start,
            "status": "scheduled",
        })
        .execute()
        .data[0]
    )


def update_tracking(event_id: str, **fields) -> None:
    client = _get_supabase()
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    (
        client.table(TRACKING_TABLE)
        .update(fields)
        .eq("outlook_event_id", event_id)
        .execute()
    )


async def schedule_bot(event: dict, meeting_url: str) -> None:
    event_id = event["id"]
    subject = event.get("subject", "Untitled Meeting")
    start_time_str = event["start"]["dateTime"]

    # Check if already scheduled
    existing = get_tracking_record(event_id)
    if existing:
        logger.info(f"Bot already scheduled for event {event_id}, skipping")
        return

    # Parse meeting start time (MS Graph returns 7 decimal places, trim to 6)
    cleaned = start_time_str.replace("Z", "+00:00")
    if "." in cleaned:
        parts = cleaned.split(".")
        frac_and_rest = parts[1]
        # Separate fractional seconds from timezone
        for i, c in enumerate(frac_and_rest):
            if not c.isdigit():
                frac = frac_and_rest[:i][:6]
                rest = frac_and_rest[i:]
                cleaned = f"{parts[0]}.{frac}{rest}"
                break
        else:
            cleaned = f"{parts[0]}.{frac_and_rest[:6]}"
    start_time = datetime.fromisoformat(cleaned)
    # If MS Graph returns naive datetime (no timezone), assume UTC
    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    dispatch_time = start_time.timestamp() - config.BOT_JOIN_BEFORE_SECONDS

    # Create tracking record (may race with a duplicate notification)
    try:
        create_tracking_record(event_id, subject, meeting_url, start_time_str)
    except Exception:
        # Unique constraint violation — another task already inserted this event
        logger.info(f"Duplicate insert for event {event_id}, skipping")
        return

    # Calculate delay
    delay = dispatch_time - now.timestamp()

    if delay <= 0:
        # Meeting already started or about to start — send bot now
        logger.info(f"Meeting '{subject}' already started, sending bot immediately")
        await _dispatch_bot(event_id, meeting_url, subject)
    else:
        logger.info(f"Scheduling bot for '{subject}' in {delay:.0f}s")
        asyncio.create_task(_delayed_dispatch(delay, event_id, meeting_url, subject))


async def _delayed_dispatch(delay: float, event_id: str, meeting_url: str, subject: str) -> None:
    await asyncio.sleep(delay)
    # Re-check status — only dispatch if still in "scheduled" state
    record = get_tracking_record(event_id)
    if not record or record["status"] != "scheduled":
        logger.info(f"Meeting '{subject}' status is '{record['status'] if record else 'missing'}', skipping bot dispatch")
        return
    await _dispatch_bot(event_id, meeting_url, subject)


_DISPATCH_RETRY_DELAYS = [60, 180, 300]  # 1 min, 3 min, 5 min


async def _dispatch_bot(event_id: str, meeting_url: str, subject: str) -> None:
    try:
        # Atomic guard: only the first task to flip scheduled→joining proceeds.
        # This prevents duplicate bots when multiple asyncio tasks target the same event.
        client = _get_supabase()
        result = (
            client.table(TRACKING_TABLE)
            .update({"status": "joining", "updated_at": datetime.now(timezone.utc).isoformat()})
            .eq("outlook_event_id", event_id)
            .eq("status", "scheduled")
            .execute()
        )
        if not result.data:
            logger.info(f"Skipping dispatch for '{subject}' — already being handled or not scheduled")
            return

        # Import here to avoid circular imports
        from .recall_service import send_bot_to_meeting

        # Retry loop: try once, then retry with increasing delays
        last_err: Exception | None = None
        for attempt, delay in enumerate([0] + _DISPATCH_RETRY_DELAYS):
            if delay:
                logger.info(f"Retry {attempt}/{len(_DISPATCH_RETRY_DELAYS)} for '{subject}' in {delay}s...")
                await asyncio.sleep(delay)
            try:
                bot_data = await send_bot_to_meeting(meeting_url)
                break  # success
            except Exception as exc:
                last_err = exc
                logger.warning(f"Attempt {attempt + 1} to send bot to '{subject}' failed: {exc}")
        else:
            # All retries exhausted
            from .slack_notifier import send_alert
            update_tracking(event_id, status="failed")
            send_alert(
                f"Meeting bot failed to join *{subject}* after "
                f"{len(_DISPATCH_RETRY_DELAYS) + 1} attempts.\nError: {last_err}"
            )
            logger.error(f"All retries exhausted for '{subject}', marked as failed")
            return

        bot_id = bot_data["id"]
        update_tracking(event_id, bot_id=bot_id, status="joined")
        logger.info(f"Bot {bot_id} sent to meeting '{subject}'")

        # Start post-meeting pipeline (waits for recording, then transcribes & summarizes)
        from .main import post_meeting_pipeline
        start_time = get_tracking_record(event_id).get("meeting_start", "")
        asyncio.create_task(post_meeting_pipeline(bot_id, event_id, subject, start_time))
    except Exception:
        update_tracking(event_id, status="failed")
        logger.exception(f"Failed to dispatch bot for event {event_id}")


async def resume_scheduled_bots() -> None:
    """Resume any bots left in 'scheduled' status after a restart."""
    client = _get_supabase()
    rows = (
        client.table(TRACKING_TABLE)
        .select("*")
        .eq("status", "scheduled")
        .execute()
        .data
    )
    if not rows:
        logger.info("No scheduled bots to resume")
        return

    now = datetime.now(timezone.utc)
    for row in rows:
        event_id = row["outlook_event_id"]
        meeting_url = row["meeting_url"]
        subject = row.get("meeting_subject", "Untitled Meeting")
        start_time_str = row.get("meeting_start", "")

        # Parse start time to calculate delay
        try:
            cleaned = start_time_str.replace("Z", "+00:00")
            if "." in cleaned:
                parts = cleaned.split(".")
                frac_and_rest = parts[1]
                for i, c in enumerate(frac_and_rest):
                    if not c.isdigit():
                        frac = frac_and_rest[:i][:6]
                        rest = frac_and_rest[i:]
                        cleaned = f"{parts[0]}.{frac}{rest}"
                        break
                else:
                    cleaned = f"{parts[0]}.{frac_and_rest[:6]}"
            start_time = datetime.fromisoformat(cleaned)
            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=timezone.utc)
            dispatch_time = start_time.timestamp() - config.BOT_JOIN_BEFORE_SECONDS
            delay = dispatch_time - now.timestamp()
        except Exception:
            logger.exception(f"Failed to parse start time '{start_time_str}' for '{subject}', skipping")
            update_tracking(event_id, status="failed")
            continue

        # If meeting_start is more than POLL_TIMEOUT_HOURS in the past, the date
        # is stale (e.g. recurring series master date).  Skip instead of dispatching.
        max_late = config.POLL_TIMEOUT_HOURS * 3600
        if delay < -max_late:
            logger.warning(
                f"Meeting '{subject}' start time {start_time_str} is more than "
                f"{config.POLL_TIMEOUT_HOURS}h in the past, skipping (likely stale series master date)"
            )
            update_tracking(event_id, status="failed")
            continue

        if delay <= 0:
            logger.info(f"Resuming bot for '{subject}' immediately (meeting already started)")
            asyncio.create_task(_dispatch_bot_task(event_id, meeting_url, subject))
        else:
            logger.info(f"Resuming bot for '{subject}' in {delay:.0f}s")
            asyncio.create_task(_delayed_dispatch(delay, event_id, meeting_url, subject))


async def _dispatch_bot_task(event_id: str, meeting_url: str, subject: str) -> None:
    """Wrapper to dispatch bot as an asyncio task."""
    await _dispatch_bot(event_id, meeting_url, subject)


async def cancel_bot(event_id: str) -> None:
    existing = get_tracking_record(event_id)
    if existing and existing["status"] == "scheduled":
        update_tracking(event_id, status="cancelled")
        logger.info(f"Cancelled bot for event {event_id}")
