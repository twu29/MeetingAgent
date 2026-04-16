import logging
import os

from . import config

logger = logging.getLogger(__name__)

_genai = None


def _get_genai():
    global _genai
    if _genai is None:
        import google.generativeai as genai
        genai.configure(api_key=os.environ.get("GOOGLE_API_KEY"))
        _genai = genai
    return _genai

SUMMARY_PROMPT = """You are an expert meeting assistant. Using the provided transcript data, create a formal meeting document in the following structured format:

**Participants:** [List all speakers mentioned in the transcript]

**Date:** {start_time}

**Meeting:** {meeting_name}

**Summary of the Meeting:**
[A concise 2-4 paragraph overview of what was discussed]

**Tasks / Action Items:**
- [Task description] (Responsible: [Name])
- [Task description] (Responsible: [Name])

**Topics & Highlights:**
1. [Topic]
   - Key Decisions: [...]
   - Challenges: [...]
   - Next Steps: [...]

Important:
- Do not make up information. Only include what is explicitly mentioned in the transcript.
- If no action items are mentioned, state "No explicit action items discussed."
- Include timestamps where relevant.

--- TRANSCRIPT ---
{transcript}
"""


def generate_meeting_summary(transcript_chunks: list[dict], metadata: dict) -> str:
    transcript_text = "\n\n".join(chunk["content"] for chunk in transcript_chunks)

    prompt = SUMMARY_PROMPT.format(
        start_time=metadata.get("start_time", "Unknown"),
        meeting_name=metadata.get("meeting_name", "Untitled Meeting"),
        transcript=transcript_text,
    )

    genai = _get_genai()
    model = genai.GenerativeModel(config.SUMMARY_MODEL)
    response = model.generate_content(prompt)

    summary = response.text
    logger.info(f"Generated summary ({len(summary)} chars) for meeting '{metadata.get('meeting_name')}'")
    return summary
