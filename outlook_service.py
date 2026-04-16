import logging
import httpx
import msal
from datetime import datetime, timedelta, timezone

from . import config

logger = logging.getLogger(__name__)

_msal_app = None


def _get_msal_app() -> msal.ConfidentialClientApplication:
    global _msal_app
    if _msal_app is None:
        _msal_app = msal.ConfidentialClientApplication(
            client_id=config.MS_CLIENT_ID,
            client_credential=config.MS_CLIENT_SECRET,
            authority=f"https://login.microsoftonline.com/{config.MS_TENANT_ID}",
        )
    return _msal_app


def get_graph_token() -> str:
    app = _get_msal_app()
    result = app.acquire_token_silent(
        scopes=["https://graph.microsoft.com/.default"], account=None
    )
    if not result:
        result = app.acquire_token_for_client(
            scopes=["https://graph.microsoft.com/.default"]
        )
    if "access_token" not in result:
        raise RuntimeError(f"Failed to acquire Graph token: {result.get('error_description', result)}")
    return result["access_token"]


def _headers() -> dict:
    return {"Authorization": f"Bearer {get_graph_token()}"}


async def create_calendar_subscription() -> dict:
    expiry = datetime.now(timezone.utc) + timedelta(minutes=config.SUBSCRIPTION_EXPIRY_MINUTES)
    body = {
        "changeType": "created,updated",
        "notificationUrl": config.MS_WEBHOOK_URL,
        "resource": f"users/{config.MS_AGENT_EMAIL}/events",
        "expirationDateTime": expiry.isoformat(),
        "clientState": config.MS_WEBHOOK_SECRET,
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{config.MS_GRAPH_BASE_URL}/subscriptions",
            headers=_headers(),
            json=body,
        )
        resp.raise_for_status()
        return resp.json()


async def renew_subscription(subscription_id: str) -> dict:
    expiry = datetime.now(timezone.utc) + timedelta(minutes=config.SUBSCRIPTION_EXPIRY_MINUTES)
    body = {"expirationDateTime": expiry.isoformat()}
    async with httpx.AsyncClient() as client:
        resp = await client.patch(
            f"{config.MS_GRAPH_BASE_URL}/subscriptions/{subscription_id}",
            headers=_headers(),
            json=body,
        )
        resp.raise_for_status()
        return resp.json()


async def get_event_details(event_id: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{config.MS_GRAPH_BASE_URL}/users/{config.MS_AGENT_EMAIL}/events/{event_id}",
            headers=_headers(),
        )
        resp.raise_for_status()
        return resp.json()


async def get_upcoming_events() -> list[dict]:
    now = datetime.now(timezone.utc)
    end_of_window = now + timedelta(days=1)
    params = {
        "startDateTime": now.isoformat(),
        "endDateTime": end_of_window.isoformat(),
        "$orderby": "start/dateTime",
        "$top": "50",
    }
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{config.MS_GRAPH_BASE_URL}/users/{config.MS_AGENT_EMAIL}/calendarView",
            headers=_headers(),
            params=params,
        )
        resp.raise_for_status()
        return resp.json().get("value", [])
