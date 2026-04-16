"""Slack notification helper — sends alerts when something fails."""

import logging
import os

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

logger = logging.getLogger(__name__)

_SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
_SLACK_ALERT_USER_ID = os.getenv("SLACK_ALERT_USER_ID", "")


def send_alert(message: str) -> None:
    """Send a DM alert to the configured Slack user."""
    if not _SLACK_BOT_TOKEN or not _SLACK_ALERT_USER_ID:
        logger.warning("Slack not configured (missing SLACK_BOT_TOKEN or SLACK_ALERT_USER_ID), skipping alert")
        return

    try:
        client = WebClient(token=_SLACK_BOT_TOKEN)
        client.chat_postMessage(channel=_SLACK_ALERT_USER_ID, text=message)
    except SlackApiError:
        logger.exception("Failed to send Slack alert")
