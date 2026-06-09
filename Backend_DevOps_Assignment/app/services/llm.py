import json
import logging
import time
from typing import Any

import google.generativeai as genai

from app.config import settings

logger = logging.getLogger(__name__)

GEMINI_MODEL = "gemini-2.5-flash"
ALLOWED_CATEGORIES = {
    "Food",
    "Shopping",
    "Travel",
    "Transport",
    "Utilities",
    "Cash Withdrawal",
    "Entertainment",
    "Other",
}


def _normalise_model_name(name: str) -> str:
    return name.removeprefix("models/")


def _gemini_model():
    model_name = _normalise_model_name(settings.gemini_model or GEMINI_MODEL)
    if model_name != GEMINI_MODEL:
        logger.warning("Ignoring unsupported GEMINI_MODEL=%s; using %s", model_name, GEMINI_MODEL)
        model_name = GEMINI_MODEL
    genai.configure(api_key=settings.gemini_api_key)
    logger.info("Using Gemini model %s", model_name)
    return genai.GenerativeModel(model_name)


def _extract_json_text(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()

    first_array = cleaned.find("[")
    first_object = cleaned.find("{")
    starts = [position for position in (first_array, first_object) if position != -1]
    if not starts:
        return cleaned

    start = min(starts)
    end_array = cleaned.rfind("]")
    end_object = cleaned.rfind("}")
    end = max(end_array, end_object)
    if end == -1 or end < start:
        return cleaned
    return cleaned[start : end + 1]


def _generate_with_retry(prompt: str, purpose: str) -> str:
    last_error: Exception | None = None
    for attempt, delay in enumerate((2, 4, 8), start=1):
        try:
            logger.info("Gemini %s call attempt %s with %s", purpose, attempt, GEMINI_MODEL)
            response = _gemini_model().generate_content(prompt)
            text = getattr(response, "text", "") or ""
            if not text.strip():
                raise ValueError("Gemini returned an empty response")
            return text
        except Exception as exc:
            last_error = exc
            logger.warning("Gemini %s attempt %s failed: %s", purpose, attempt, exc)
            if attempt < 3:
                time.sleep(delay)

    raise RuntimeError(f"Gemini {purpose} failed after 3 attempts: {last_error}")


def classify_transactions(transactions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    payload = [
        {
            "txn_index": item["txn_index"],
            "merchant": item.get("merchant"),
            "amount": item.get("amount"),
            "currency": item.get("currency"),
        }
        for item in transactions
    ]
    prompt = f"""You are a financial transaction categoriser.

Classify each transaction into exactly one of these categories:
Food, Shopping, Travel, Transport, Utilities, Cash Withdrawal, Entertainment, Other

Return ONLY a valid JSON array with no extra text, markdown, or explanation.
Each element must be an object: {{"txn_index": <int>, "category": "<category>"}}

Transactions to classify:
{json.dumps(payload, ensure_ascii=True)}
"""
    raw_text = _generate_with_retry(prompt, "classification")
    parsed = json.loads(_extract_json_text(raw_text))
    if not isinstance(parsed, list):
        raise ValueError("Classification response was not a JSON array")

    cleaned: list[dict[str, Any]] = []
    for item in parsed:
        txn_index = int(item["txn_index"])
        category = str(item["category"]).strip()
        if category not in ALLOWED_CATEGORIES:
            category = "Other"
        cleaned.append({"txn_index": txn_index, "category": category})
    return cleaned, raw_text


def generate_summary(summary_data: dict[str, Any]) -> tuple[dict[str, Any], str]:
    prompt = f"""You are a financial analyst. Given the following transaction summary data, produce a JSON
object with exactly these fields:
- total_spend_inr: number (sum of all INR transaction amounts with status SUCCESS)
- total_spend_usd: number (sum of all USD transaction amounts with status SUCCESS)
- top_merchants: array of 3 objects {{merchant: string, total_amount: number}}, sorted desc
- anomaly_count: number
- narrative: string (2-3 sentences describing spending patterns and risks)
- risk_level: string, one of "low", "medium", "high"

Return ONLY valid JSON. No markdown, no explanation.

Data:
{json.dumps(summary_data, ensure_ascii=True)}
"""
    raw_text = _generate_with_retry(prompt, "summary")
    parsed = json.loads(_extract_json_text(raw_text))
    if not isinstance(parsed, dict):
        raise ValueError("Summary response was not a JSON object")
    return parsed, raw_text
