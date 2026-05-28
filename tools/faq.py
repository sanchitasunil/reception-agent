from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

import lancedb
from livekit.agents.llm import function_tool
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = str(_ROOT / ".lancedb")
TABLE_NAME = "clinic_faq"
FAQ_PATH = _ROOT / "knowledge" / "clinic_faq.md"
MODEL_NAME = "all-MiniLM-L6-v2"
# Cosine distance (0 = identical, 2 = opposite). LanceDB defaults to L2 otherwise.
VECTOR_METRIC = "cosine"
# Tuned for all-MiniLM-L6-v2 + clinic_faq chunks (0.72 is too strict even with cosine).
DISTANCE_THRESHOLD = 0.83
MAX_RESPONSE_CHARS = 500
_SEARCH_LIMIT = 4

# Query terms → heading substring; lowers effective distance when both match.
_HEADING_BOOSTS: list[tuple[tuple[str, ...], str]] = [
    (("timing", "timings", "hour", "hours", "open", "closed", "sunday", "holiday"), "clinic hours"),
    (("fee", "fees", "cost", "price", "charge", "follow-up", "follow up", "consultation"), "consultation fees"),
    (("insurance", "payment", "card", "cashless", "cash"), "payments"),
    (("park", "parking"), "parking"),
    (("x-ray", "xray", "ecg", "ultrasound", "lab", "blood test", "diagnostic"), "lab and diagnostic"),
    (("pharmacy", "medicine"), "pharmacy"),
    (("emergency", "urgent"), "emergencies"),
    (("doctor", "sarah", "james", "lin", "cole", "physician"), "our doctors"),
    (("address", "location", "find", "directions", "where"), "location"),
    (("cancel", "reschedule", "book", "appointment", "walk-in"), "booking"),
    (("phone", "whatsapp", "email", "contact"), "contact"),
]


def prefetch_embedding_model() -> None:
    """Pre-download embedding weights (used by agent.py download-files)."""
    try:
        SentenceTransformer(MODEL_NAME)
        logger.info("Embedding model %s ready", MODEL_NAME)
    except Exception as exc:
        logger.error("Embedding model prefetch failed: %s", exc)


def _chunk_markdown(text: str) -> list[dict]:
    """Split markdown by H2 sections; each section becomes one searchable chunk."""
    chunks: list[dict] = []
    parts = re.split(r"\n(?=## )", text.strip())

    for part in parts:
        part = part.strip()
        if not part.startswith("## "):
            continue

        lines = part.split("\n", 1)
        heading = lines[0].strip()
        body = lines[1].strip() if len(lines) > 1 else ""
        if len(body) < 30:
            continue

        full = f"{heading}\n\n{body}"
        chunks.append({"heading": heading, "body": body, "full": full})

    return chunks


def _heading_boost(query: str, heading: str) -> float:
    """Subtract from cosine distance when query intent matches section heading."""
    q = query.lower()
    h = heading.lower()
    for keywords, hint in _HEADING_BOOSTS:
        if hint in h and any(k in q for k in keywords):
            return 0.25
    return 0.0


def _boosted_distance(query: str, row: dict) -> float:
    return row.get("_distance", 2.0) - _heading_boost(query, row.get("heading", ""))


def _trim_at_sentence(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    boundary = cut.rfind(". ")
    if boundary >= int(max_chars * 0.55):
        return cut[: boundary + 1]
    return cut.rstrip() + "..."


def _pick_chunks(query: str, results: list[dict]) -> list[dict]:
    relevant = [r for r in results if r.get("_distance", 2.0) < DISTANCE_THRESHOLD]
    if not relevant:
        return []

    ranked = sorted(relevant, key=lambda r: _boosted_distance(query, r))
    best = ranked[0]
    if len(ranked) == 1:
        return [best]

    gap = _boosted_distance(query, ranked[1]) - _boosted_distance(query, best)
    if _heading_boost(query, best.get("heading", "")) > 0 or gap > 0.06:
        return [best]
    return ranked[:2]


def build_index() -> None:
    """Build or rebuild the LanceDB vector index from clinic_faq.md."""
    try:
        if not FAQ_PATH.is_file():
            logger.error("FAQ file not found: %s", FAQ_PATH)
            return

        text = FAQ_PATH.read_text(encoding="utf-8")
        chunk_list = _chunk_markdown(text)
        if not chunk_list:
            logger.error("No FAQ chunks produced from %s", FAQ_PATH)
            return

        model = SentenceTransformer(MODEL_NAME)
        vectors = model.encode([c["full"] for c in chunk_list])

        data = [
            {
                "heading": chunk["heading"],
                "body": chunk["body"],
                "full": chunk["full"],
                "vector": vectors[i].tolist(),
            }
            for i, chunk in enumerate(chunk_list)
        ]

        db = lancedb.connect(DB_PATH)
        if TABLE_NAME in db.table_names():
            db.drop_table(TABLE_NAME)
        db.create_table(TABLE_NAME, data)

        logger.info("FAQ index built: %d chunks", len(chunk_list))
    except Exception as exc:
        logger.error("FAQ index build failed: %s", exc)


@function_tool()
async def search_faq(query: str) -> str:
    """
    Search the clinic knowledge base to answer patient questions.
    Call this for ANY factual question about the clinic: hours, location,
    fees, doctors, lab services, parking, payments, cancellation policy,
    pharmacy, or emergencies.
    Do not guess or answer from memory — always call this tool first.
    query: the patient's question exactly as they asked it.
    """

    def _search(q: str) -> str:
        try:
            model = SentenceTransformer(MODEL_NAME)
            vec = model.encode([q])[0].tolist()

            db = lancedb.connect(DB_PATH)
            if TABLE_NAME not in db.table_names():
                return ""

            results = (
                db.open_table(TABLE_NAME)
                .search(vec)
                .metric(VECTOR_METRIC)
                .limit(_SEARCH_LIMIT)
                .to_list()
            )

            picked = _pick_chunks(q, results)
            if not picked:
                return ""

            combined = "\n\n".join(r["body"] for r in picked)
            return _trim_at_sentence(combined, MAX_RESPONSE_CHARS)

        except Exception as exc:
            logger.error("FAQ search error: %s", exc)
            return ""

    result = await asyncio.to_thread(_search, query)
    if not result:
        return (
            "I don't have specific information on that. "
            "Let me have someone from our team call you back with the answer."
        )
    return result
