import os
from dotenv import load_dotenv

load_dotenv()

# Microsoft Graph
MS_TENANT_ID = os.environ["MS_TENANT_ID"]
MS_CLIENT_ID = os.environ["MS_CLIENT_ID"]
MS_CLIENT_SECRET = os.environ["MS_CLIENT_SECRET"]
MS_AGENT_EMAIL = os.getenv("MS_AGENT_EMAIL", "upa@captus.ai")
MS_WEBHOOK_URL = os.environ.get("MS_WEBHOOK_URL", "")
MS_WEBHOOK_SECRET = os.getenv("MS_WEBHOOK_SECRET", "meeting-agent-secret")
MS_GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"

# Recall.ai
RECALL_API_KEY = os.environ["RECALL_API_KEY"]
RECALL_BOT_NAME = "Captus"
RECALL_BASE_URL = "https://us-west-2.recall.ai/api/v1"

# Timing
BOT_JOIN_BEFORE_SECONDS = 60
POLL_INTERVAL_SECONDS = 60
POLL_TIMEOUT_HOURS = 4
TRANSCRIPT_WAIT_SECONDS = 120
CALENDAR_SYNC_INTERVAL_SECONDS = 4 * 3600  # re-sync calendar every 4 hours

# Vector store
CHUNK_SIZE = 2000
CHUNK_OVERLAP = 400

# Project-based table routing
UPA3_TABLE = "upa3_0"
UPA5_TABLE = "upa5_0"

# Keywords that map to each project table (matched case-insensitively against meeting subject)
_UPA3_KEYWORDS = ["ofs", "laboratory", "upa 3.0", "upa3"]
_UPA5_KEYWORDS = ["parking garage", "upa 5.0", "upa5"]


def resolve_target_tables(subject: str) -> list[str]:
    """Determine which Supabase table(s) to store meeting data in based on subject."""
    lower = subject.lower()
    is_upa3 = any(kw in lower for kw in _UPA3_KEYWORDS)
    is_upa5 = any(kw in lower for kw in _UPA5_KEYWORDS)

    if is_upa3 and not is_upa5:
        return [UPA3_TABLE]
    if is_upa5 and not is_upa3:
        return [UPA5_TABLE]
    # Both matched, or neither matched (unclear) — store in both
    return [UPA3_TABLE, UPA5_TABLE]

# Models
SUMMARY_MODEL = "models/gemini-2.5-flash"
EMBEDDING_MODEL = "textembedding-gecko"

# Subscription renewal — MS Graph subscriptions expire after max 3 days for calendar
SUBSCRIPTION_EXPIRY_MINUTES = 4230  # ~2.9 days, renew before expiry
