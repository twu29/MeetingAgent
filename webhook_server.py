import logging
import re
from fastapi import APIRouter, BackgroundTasks, Query, Request, Response

from . import config
from .outlook_service import create_calendar_subscription, get_event_details, get_upcoming_events
from .bot_scheduler import cancel_bot, schedule_bot, get_tracking_record

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook")

# Track processed events to prevent duplicate bot dispatches
_processed_events: set[str] = set()


def extract_url_from_text(text: str) -> str | None:
    pattern = r'https?://[^\s<>"\')\]]*(?:teams|zoom|meet\.google)[^\s<>"\')\]]*'
    match = re.search(pattern, text, re.IGNORECASE)
    return match.group(0) if match else None


def extract_meeting_url(event: dict) -> str | None:
    # 1. Check Teams online meeting
    join_url = event.get("onlineMeeting", {})
    if join_url and join_url.get("joinUrl"):
        return join_url["joinUrl"]

    # 2. Check location field
    location = event.get("location", {}).get("displayName", "")
    url = extract_url_from_text(location)
    if url:
        return url

    # 3. Check body content
    body = event.get("body", {}).get("content", "")
    url = extract_url_from_text(body)
    if url:
        return url

    return None


@router.post("/outlook")
async def outlook_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    validationToken: str | None = Query(default=None),
):
    # Validation handshake — MS Graph sends this when creating a subscription
    if validationToken:
        return Response(content=validationToken, media_type="text/plain")

    # Parse notification payload
    payload = await request.json()
    notifications = payload.get("value", [])

    for notification in notifications:
        # Validate client state
        if notification.get("clientState") != config.MS_WEBHOOK_SECRET:
            continue

        event_id = notification.get("resourceData", {}).get("id")
        if not event_id:
            continue

        # Dispatch background processing
        background_tasks.add_task(process_event, event_id)

    # Must respond within 3 seconds
    return Response(status_code=202)


async def process_event(event_id: str) -> None:
    try:
        # Fetch full event details
        event = await get_event_details(event_id)

        # Check if meeting was cancelled — cancel bot if previously scheduled
        if event.get("isCancelled"):
            logger.info(f"Event {event_id} is cancelled, cancelling bot")
            await cancel_bot(event_id)
            _processed_events.discard(event_id)
            return

        # Series master events carry the original series start date
        # instead of the next instance date.  Delegate to calendar
        # sync which uses calendarView and returns correct per-instance dates.
        if event.get("type") == "seriesMaster":
            logger.info(f"Event {event_id} is a recurring series master, running calendar sync instead")
            await sync_upcoming_meetings()
            return

        # Idempotency check (only for non-cancelled events)
        if event_id in _processed_events:
            logger.info(f"Event {event_id} already processed, skipping")
            return

        # Check: is this a future meeting?
        start_time = event.get("start", {}).get("dateTime", "")
        if not start_time:
            logger.info(f"Event {event_id} has no start time, skipping")
            return

        # Check: does it have an online meeting URL?
        meeting_url = extract_meeting_url(event)
        if not meeting_url:
            logger.info(f"Event {event_id} has no meeting URL, skipping")
            return

        # Mark as processed
        _processed_events.add(event_id)

        subject = event.get("subject", "Untitled Meeting")
        logger.info(f"New meeting detected: '{subject}' — {meeting_url}")

        # Schedule bot to join the meeting
        await schedule_bot(event, meeting_url)

    except Exception:
        logger.exception(f"Error processing event {event_id}")

# Fetch today's upcoming meetings and schedule bots for any not already tracked.
async def sync_upcoming_meetings() -> None:
    try:
        events = await get_upcoming_events()
        logger.info(f"Calendar sync: found {len(events)} upcoming events today")
    except Exception:
        logger.exception("Calendar sync: failed to fetch upcoming events")
        return

    for event in events:
        event_id = event.get("id")
        if not event_id:
            continue

        if event.get("isCancelled"):
            continue

        # Skip if already tracked
        if get_tracking_record(event_id):
            continue

        # Skip if already in the in-memory processed set
        if event_id in _processed_events:
            continue

        meeting_url = extract_meeting_url(event)
        if not meeting_url:
            continue

        start_time = event.get("start", {}).get("dateTime", "")
        if not start_time:
            continue

        subject = event.get("subject", "Untitled Meeting")
        logger.info(f"Calendar sync: scheduling bot for '{subject}'")
        _processed_events.add(event_id)

        try:
            await schedule_bot(event, meeting_url)
        except Exception:
            logger.exception(f"Calendar sync: failed to schedule bot for '{subject}'")


@router.post("/outlook/setup")
async def setup_subscription():
    result = await create_calendar_subscription()
    subscription_id = result.get("id")
    logger.info(f"Created calendar subscription: {subscription_id}")
    return {"subscription_id": subscription_id, "status": "active"}
