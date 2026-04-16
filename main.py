import asyncio
import logging
import os

from fastapi import FastAPI
import uvicorn

from .webhook_server import router, sync_upcoming_meetings
from .outlook_service import create_calendar_subscription, renew_subscription
from .bot_scheduler import update_tracking, resume_scheduled_bots
from . import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Meeting Agent")
app.include_router(router)

# Store subscription ID for renewal
_subscription_id: str | None = None


async def post_meeting_pipeline(bot_id: str, event_id: str, subject: str, start_time: str) -> None:
    try:
        # Lazy-import heavy modules — only needed after meeting ends
        from .recall_service import wait_for_recording
        from .transcript_service import get_transcript_with_retry
        from .transcript_cleaner import clean_transcript
        from .vector_store import store_transcript_chunks, store_summary_chunks, query_transcript_chunks
        from .summary_agent import generate_meeting_summary

        update_tracking(event_id, status="recording")

        # Wait for recording to be ready
        bot_data = await wait_for_recording(bot_id)

        # Create and download transcript
        recording_id = bot_data["recordings"][0]["id"]
        transcript_id, raw_transcript = await get_transcript_with_retry(recording_id)

        # Clean transcript
        cleaned_text, _ = clean_transcript(raw_transcript)

        # Determine target tables based on meeting subject
        target_tables = config.resolve_target_tables(subject)
        logger.info(f"Meeting '{subject}' -> storing in table(s): {target_tables}")

        # Store transcript in vector DB
        metadata = {
            "bot_id": bot_id,
            "transcript_id": transcript_id,
            "source": "meeting_agent",
            "meeting_name": subject,
            "start_time": start_time,
        }
        store_transcript_chunks(cleaned_text, metadata, tables=target_tables)

        # Generate and store summary (read from the first target table)
        chunks = query_transcript_chunks(bot_id, table=target_tables[0])
        summary = generate_meeting_summary(chunks, metadata)
        store_summary_chunks(summary, metadata, tables=target_tables)

        update_tracking(event_id, status="completed")
        logger.info(f"Meeting pipeline completed for '{subject}'")

    except Exception:
        update_tracking(event_id, status="failed")
        logger.exception(f"Post-meeting pipeline failed for event {event_id}")


async def calendar_sync_loop() -> None:
    """Periodically re-sync the calendar so recurring-meeting instances are caught."""
    while True:
        await asyncio.sleep(config.CALENDAR_SYNC_INTERVAL_SECONDS)
        logger.info("Periodic calendar sync starting")
        try:
            await sync_upcoming_meetings()
        except Exception:
            logger.exception("Periodic calendar sync failed")


async def bot_cleanup_loop() -> None:
    """Delete Recall AI bots older than 6 months, once per day."""
    while True:
        await asyncio.sleep(24 * 3600)  # run once a day
        try:
            from .recall_service import cleanup_old_bots
            deleted = await cleanup_old_bots(max_age_days=180)
            logger.info(f"Daily cleanup: removed {deleted} old bot(s) from Recall AI")
        except Exception:
            logger.exception("Daily bot cleanup failed")


async def subscription_renewal_loop() -> None:
    global _subscription_id
    # Renew every 2 days (subscription expires after ~3 days)
    renewal_interval = 2 * 24 * 3600

    while True:
        await asyncio.sleep(renewal_interval)
        if _subscription_id:
            try:
                await renew_subscription(_subscription_id)
                logger.info(f"Renewed subscription {_subscription_id}")
            except Exception:
                logger.exception("Failed to renew subscription, creating new one")
                try:
                    result = await create_calendar_subscription()
                    _subscription_id = result.get("id")
                    logger.info(f"Created new subscription {_subscription_id}")
                except Exception:
                    logger.exception("Failed to create new subscription")


@app.on_event("startup")
async def startup():
    global _subscription_id
    try:
        result = await create_calendar_subscription()
        _subscription_id = result.get("id")
        logger.info(f"Calendar subscription active: {_subscription_id}")
    except Exception:
        logger.warning("Could not create calendar subscription — check MS Graph permissions")

    # Resume any bots that were scheduled before the restart (run first so
    # sync_upcoming_meetings sees them as already tracked and skips them)
    await resume_scheduled_bots()

    # Sync calendar to catch meetings missed during downtime
    await sync_upcoming_meetings()

    asyncio.create_task(subscription_renewal_loop())
    asyncio.create_task(calendar_sync_loop())
    asyncio.create_task(bot_cleanup_loop())


def run():
    port = int(os.environ.get("PORT", 8001))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    run()
