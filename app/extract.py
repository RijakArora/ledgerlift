"""Document extraction via Gemini's native document/vision understanding.

Design notes:
- Sends the uploaded file bytes straight to Gemini as inline data. No
  separate OCR engine, no PDF->image conversion step: Gemini accepts
  application/pdf and common image types directly. That's the core
  cost/complexity advantage over a traditional OCR + parser pipeline.
- Uses Gemini's structured-output mode (responseSchema) rather than
  asking for JSON in the prompt and hoping. The model is constrained to
  the schema, so we don't have to defensively parse prose.
- The model is configured via GEMINI_MODEL and defaults to a "-latest"
  alias, never a pinned version. Pinned Gemini/Groq model names broke
  twice during this project when providers retired them.
"""
import base64
import json
import logging
import os
import time

import httpx

log = logging.getLogger(__name__)

# Gemini returns transient 503 "high demand" fairly regularly (hit during
# this project's very first end-to-end upload test). Making the user retry
# a two-second blip is unacceptable for a paid tool, so retry internally.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
ATTEMPTS_PER_MODEL = 2  # then fall through to the next model in the chain

API_BASE = "https://generativelanguage.googleapis.com/v1beta"

# A chain, not a single model. The whole product depends on this one call
# succeeding, and a single model *will* be congested sometimes: during
# first testing, gemini-flash-latest returned 503 "high demand" on three
# consecutive attempts while gemini-flash-lite-latest answered fine. Same
# reasoning as the provider chain in ai_business_platform's llm_router.
#
# Only "-latest"-style aliases are used deliberately. Pinned names get
# retired without warning - gemini-2.5-flash already returns 404 for this
# API key, and two pinned defaults broke earlier in this project.
DEFAULT_MODEL_CHAIN = "gemini-flash-latest,gemini-flash-lite-latest,gemini-3.5-flash"

SUPPORTED_MIME = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/webp",
}

# Constrains the model's output. Everything is nullable on purpose: a
# crumpled receipt genuinely may not have an invoice number or due date,
# and a null is far more useful than a hallucinated value.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "document_type": {"type": "string", "enum": ["invoice", "receipt", "other"]},
        "vendor_name": {"type": "string", "nullable": True},
        "vendor_address": {"type": "string", "nullable": True},
        "invoice_number": {"type": "string", "nullable": True},
        "invoice_date": {"type": "string", "nullable": True, "description": "ISO YYYY-MM-DD if determinable"},
        "due_date": {"type": "string", "nullable": True, "description": "ISO YYYY-MM-DD if determinable"},
        "currency": {"type": "string", "nullable": True, "description": "ISO code e.g. USD"},
        "subtotal": {"type": "number", "nullable": True},
        "tax": {"type": "number", "nullable": True},
        "shipping": {"type": "number", "nullable": True},
        "total": {"type": "number", "nullable": True},
        "line_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "nullable": True},
                    "quantity": {"type": "number", "nullable": True},
                    "unit_price": {"type": "number", "nullable": True},
                    "amount": {"type": "number", "nullable": True},
                },
            },
        },
    },
    "required": ["document_type"],
}

PROMPT = (
    "Extract the structured data from this invoice or receipt. "
    "Return numbers as plain numbers with no currency symbols or thousands separators. "
    "Convert dates to YYYY-MM-DD. "
    "If a field genuinely is not present on the document, return null for it - "
    "do not guess, infer, or fabricate a value."
)


class ExtractionError(Exception):
    """Raised when extraction could not be completed. The message is shown
    to the user, so it must be plain and actionable."""


def _api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise ExtractionError(
            "Server is not configured for extraction (missing GEMINI_API_KEY). "
            "This is our problem, not yours - please try again later."
        )
    return key


def extract_document(file_bytes: bytes, mime_type: str, timeout: float = 90.0) -> dict:
    """Extracts structured fields from an invoice/receipt.

    Raises ExtractionError with a user-readable message on failure rather
    than returning half-filled data that looks like a successful read.
    """
    mime_type = (mime_type or "").lower()
    if mime_type == "image/jpg":
        mime_type = "image/jpeg"
    if mime_type not in SUPPORTED_MIME:
        raise ExtractionError(
            f"Unsupported file type '{mime_type or 'unknown'}'. "
            "Upload a PDF, PNG, JPG, or WEBP."
        )

    payload = {
        "contents": [{
            "parts": [
                {"text": PROMPT},
                {"inline_data": {"mime_type": mime_type, "data": base64.b64encode(file_bytes).decode()}},
            ]
        }],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
            "temperature": 0,
        },
    }

    key = _api_key()
    chain = [m.strip() for m in os.environ.get("GEMINI_MODEL_CHAIN", DEFAULT_MODEL_CHAIN).split(",") if m.strip()]

    resp = None
    last_status = None
    for model in chain:
        url = f"{API_BASE}/models/{model}:generateContent?key={key}"
        delay = 1.5
        for attempt in range(1, ATTEMPTS_PER_MODEL + 1):
            try:
                resp = httpx.post(url, json=payload, timeout=timeout)
            except httpx.TimeoutException:
                log.warning("%s timed out, trying next model", model)
                resp, last_status = None, "timeout"
                break
            except httpx.HTTPError as e:
                log.warning("%s transport error (%s), trying next model", model, e)
                resp, last_status = None, "transport"
                break

            if resp.status_code == 200:
                log.info("Extraction served by %s", model)
                break

            last_status = resp.status_code
            if resp.status_code in RETRYABLE_STATUS and attempt < ATTEMPTS_PER_MODEL:
                log.warning("%s returned %s (attempt %d), retrying in %.1fs",
                            model, resp.status_code, attempt, delay)
                time.sleep(delay)
                delay *= 2
                continue
            break

        if resp is not None and resp.status_code == 200:
            break
        log.warning("%s exhausted (last status %s), falling through to next model", model, last_status)
        resp = None

    if resp is None or resp.status_code != 200:
        if resp is not None and resp.status_code not in RETRYABLE_STATUS:
            raise ExtractionError(f"Extraction failed (HTTP {resp.status_code}). {resp.text[:200]}")
        raise ExtractionError(
            "Every extraction model we use is busy right now. We tried several and retried each - "
            "please try again in a minute. Your document was not charged against your quota.")

    try:
        candidates = resp.json()["candidates"]
        text = candidates[0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, ValueError) as e:
        # A blocked/empty response lands here - report honestly instead of
        # returning an empty record that looks like a clean extraction.
        log.warning("Unexpected Gemini response shape: %s | %s", e, resp.text[:300])
        raise ExtractionError("The extraction service returned an unreadable response. Please try again.")

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        log.warning("Model returned non-JSON despite schema: %s", text[:300])
        raise ExtractionError("Could not read structured data from that document. Try a clearer scan.")
