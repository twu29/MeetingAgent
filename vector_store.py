import json
import logging
import os

from . import config

logger = logging.getLogger(__name__)

_vertex_initialized = False


def _init_vertex():
    """Initialize Vertex AI lazily, supporting both file path and JSON env var for credentials."""
    global _vertex_initialized
    if _vertex_initialized:
        return
    import tempfile
    from google.cloud import aiplatform
    from google.oauth2 import service_account

    creds_path = os.environ.get("GOOGLE_VERTEX_CREDENTIALS_PATH", "")
    creds_json = os.environ.get("GOOGLE_VERTEX_CREDENTIALS_JSON", "")

    if creds_json and not os.path.exists(creds_path or ""):
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        tmp.write(creds_json)
        tmp.close()
        creds_path = tmp.name

    credentials = None
    if creds_path and os.path.exists(creds_path):
        credentials = service_account.Credentials.from_service_account_file(creds_path)

    aiplatform.init(
        project=os.environ.get("GOOGLE_VERTEX_PROJECT_ID", "uplifted-env-465921-r1"),
        location="us-central1",
        credentials=credentials,
    )
    _vertex_initialized = True


def _get_supabase():
    from supabase import create_client
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def _get_embeddings(texts: list[str]) -> list[list[float]]:
    _init_vertex()
    from google.cloud import aiplatform
    endpoint = aiplatform.Endpoint.list(
        filter=f'display_name="{config.EMBEDDING_MODEL}"'
    )
    if not endpoint:
        from vertexai.language_models import TextEmbeddingModel
        model = TextEmbeddingModel.from_pretrained("text-embedding-004")
        embeddings = model.get_embeddings(texts)
        return [e.values for e in embeddings]
    raise RuntimeError("Could not load embedding model")


def _chunk_text(text: str) -> list[str]:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=config.CHUNK_SIZE,
        chunk_overlap=config.CHUNK_OVERLAP,
    )
    return splitter.split_text(text)


def store_transcript_chunks(cleaned_text: str, metadata: dict, tables: list[str] | None = None) -> None:
    if tables is None:
        tables = [config.UPA3_TABLE, config.UPA5_TABLE]
    chunks = _chunk_text(cleaned_text)
    embeddings = _get_embeddings(chunks)
    client = _get_supabase()

    for table in tables:
        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
            row = {
                "content": chunk,
                "embedding": embedding,
                "metadata": json.dumps({
                    **metadata,
                    "chunk_index": i,
                    "type": "transcript",
                }),
            }
            client.table(table).insert(row).execute()
        logger.info(f"Stored {len(chunks)} transcript chunks in '{table}' for bot {metadata.get('bot_id')}")


def store_summary_chunks(summary_text: str, metadata: dict, tables: list[str] | None = None) -> None:
    if tables is None:
        tables = [config.UPA3_TABLE, config.UPA5_TABLE]
    chunks = _chunk_text(summary_text)
    embeddings = _get_embeddings(chunks)
    client = _get_supabase()

    for table in tables:
        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
            row = {
                "content": chunk,
                "embedding": embedding,
                "metadata": json.dumps({
                    **metadata,
                    "chunk_index": i,
                    "type": "summary",
                }),
            }
            client.table(table).insert(row).execute()
        logger.info(f"Stored {len(chunks)} summary chunks in '{table}' for bot {metadata.get('bot_id')}")


def query_transcript_chunks(bot_id: str, table: str | None = None) -> list[dict]:
    if table is None:
        table = config.UPA3_TABLE
    client = _get_supabase()
    rows = (
        client.table(table)
        .select("id, content, metadata")
        .filter("metadata->>bot_id", "eq", bot_id)
        .filter("metadata->>source", "eq", "meeting_agent")
        .filter("metadata->>type", "eq", "transcript")
        .order("id")
        .execute()
        .data
    )
    return rows
