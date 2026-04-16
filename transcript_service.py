import asyncio
import logging

import httpx

from . import config

logger = logging.getLogger(__name__)


def _headers() -> dict:
    return {"Authorization": f"Token {config.RECALL_API_KEY}"}


async def create_transcript(recording_id: str) -> dict:
    body = {
        "provider": {
            "assembly_ai_async": {"language": "en"}
        }
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{config.RECALL_BASE_URL}/recording/{recording_id}/create_transcript/",
            headers=_headers(),
            json=body,
        )
        resp.raise_for_status()
        return resp.json()


async def get_transcript(transcript_id: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{config.RECALL_BASE_URL}/transcript/{transcript_id}/",
            headers=_headers(),
        )
        resp.raise_for_status()
        return resp.json()


async def download_transcript(download_url: str) -> list[dict]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(download_url)
        resp.raise_for_status()
        return resp.json()


async def get_transcript_with_retry(recording_id: str) -> tuple[str, list[dict]]:
    # Create transcript
    result = await create_transcript(recording_id)
    transcript_id = result["id"]
    logger.info(f"Transcript {transcript_id} created, waiting for processing...")

    # Wait for transcript to be processed
    await asyncio.sleep(config.TRANSCRIPT_WAIT_SECONDS)

    # Get transcript metadata
    metadata = await get_transcript(transcript_id)
    download_url = metadata.get("data", {}).get("download_url")

    if not download_url:
        # Wait a bit more and retry
        logger.info("Transcript not ready yet, waiting another 2 min...")
        await asyncio.sleep(config.TRANSCRIPT_WAIT_SECONDS)
        metadata = await get_transcript(transcript_id)
        download_url = metadata.get("data", {}).get("download_url")

    if not download_url:
        raise RuntimeError(f"Transcript {transcript_id} has no download URL after retries")

    # Download raw transcript data
    raw_data = await download_transcript(download_url)
    logger.info(f"Transcript {transcript_id} downloaded ({len(raw_data)} segments)")

    return transcript_id, raw_data
