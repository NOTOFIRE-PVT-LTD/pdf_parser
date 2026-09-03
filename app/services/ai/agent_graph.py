"""LangGraph agent — general chat + tender tools + RAG memory."""

from __future__ import annotations

import json
import logging
import re
import urllib.error
from typing import Any, Literal, TypedDict

from langgraph.graph import END, StateGraph

from app.config import Settings, get_settings
from app.models.schemas import ProductItem
from app.services.ai.gemini_client import generate_with_fallback
from app.services.ai.memory_store import get_memory_store
from app.services.ai.mistral_client import chat_completion
from app.services.ai.token_budget import (
    clip_text,
    compact_history,
    looks_like_tender_task,
)
from app.services.name_learning import get_name_learning
from app.services.product_sanitize import _renumber_sequential
from app.utils.product_name import is_work_description, recheck_product_name

logger = logging.getLogger(__name__)

_CHAT_SYSTEM = """You are Notofire AI — a friendly, capable assistant (like Claude).
You can talk about anything: coding, ideas, explanations, general chat.
You also help with Indian Railway / tender BOQ PDFs when the user uploads them:
extract products, clean names, fix rows, export Excel.

LANGUAGE (critical — follow strictly):
- Detect the language of the user's LATEST message and reply ONLY in that language.
- If the user writes in English → reply in clear English only (no Hindi words, no Hinglish).
- If the user writes in Hindi (Devanagari) → reply in Hindi only.
- If the user writes in Hinglish (mixed Hindi+English roman script) → reply in Hinglish.
- Do not default to Hinglish. Match the user exactly.

Other rules:
- Reply naturally. Do NOT push PDF upload unless they ask about tenders/PDFs.
- Keep answers concise unless they want detail.
- If tender product rows are in context and they ask to fix them, help.
- Use remembered context snippets when relevant.
"""

_FIX_SYSTEM = """You are Notofire AI fixing tender BOQ extraction rows.
Return ONLY valid JSON (no markdown fences):
{"reply":"short friendly answer","changes":[{"index":0,"product_name":"...","remove":false}],"summary":"one line or empty"}

LANGUAGE for "reply" and "summary":
- Match the user's latest message language exactly.
- English message → English reply. Hindi → Hindi. Hinglish → Hinglish.
- Never default to Hinglish.

Rules:
- product_name MUST use words from that row's description only
- Remove scope phrases: supply, installation, testing, commissioning, etc.
- Set remove=true for junk OR WORK rows (not products): excavation/trench/refilling,
  platform/track reinstatement, payment notes, "work includes", civil execution paragraphs.
  Keep ONLY actual supplyable materials / equipment / products.
- When user says items are work (not products), remove those rows (remove=true).
- Include only rows that need a change; [] if none
- Follow the user instruction; use chat history for follow-ups
"""


class AgentState(TypedDict, total=False):
    instruction: str
    history: list[dict[str, str]]
    products: list[dict[str, Any]]
    memory: list[str]
    intent: str
    reply: str
    changes: list[dict[str, Any]]
    summary: str
    error: str
    provider: str
    chat_id: str


def _compact_rows(products: list[ProductItem]) -> list[dict]:
    rows = []
    for i, p in enumerate(products):
        rows.append({
            "index": i,
            "s_no": p.s_no,
            "product_name": clip_text(p.product_name or "", 120),
            "description": clip_text(p.description or "", 220),
            "qty": p.item_qty,
            "amount": p.amount,
        })
    return rows


def _validate_name(desc: str, name: str) -> bool:
    if not name or not desc or len(name.strip()) < 2:
        return False
    nl, dl = name.lower(), desc.lower()
    if nl in dl:
        return True
    words = [w for w in re.findall(r"\w+", nl) if len(w) > 2]
    if not words:
        return False
    return sum(1 for w in words if w in dl) / len(words) >= 0.55


def _parse_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    return json.loads(text)


def _call_llm(
    settings: Settings,
    messages: list[dict[str, str]],
    *,
    json_mode: bool,
    temperature: float,
) -> tuple[str, str]:
    """Returns (content, provider). Prefers Mistral, falls back to Gemini on 429."""
    provider = "mistral" if settings.mistral_api_key else "gemini"
    if not settings.mistral_api_key and not settings.gemini_api_key:
        raise ValueError("no_key")

    if provider == "mistral":
        try:
            return (
                chat_completion(
                    settings.mistral_api_key or "",
                    messages,
                    model=settings.mistral_model,
                    base_url=settings.mistral_base_url,
                    temperature=temperature,
                    json_mode=json_mode,
                    timeout=90,
                ),
                "mistral",
            )
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or not settings.gemini_api_key:
                raise
            provider = "gemini"

    # Gemini path — fold messages into one prompt
    system = ""
    parts = []
    for m in messages:
        if m["role"] == "system":
            system = m["content"]
        else:
            parts.append(f"{m['role']}: {m['content']}")
    payload = {
        "system_instruction": {"parts": [{"text": system or _CHAT_SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": "\n".join(parts)}]}],
        "generationConfig": {
            "temperature": temperature,
            **({"responseMimeType": "application/json"} if json_mode else {}),
        },
    }
    raw = generate_with_fallback(
        settings.gemini_api_key or "",
        settings.gemini_model,
        payload,
        base_url=settings.gemini_base_url,
        timeout=75,
        model_cache=[],
    )
    return raw, "gemini"


def node_retrieve(state: AgentState) -> AgentState:
    try:
        mem = get_memory_store()
        hits = mem.search(
            state.get("instruction") or "",
            k=3,
            chat_id=state.get("chat_id"),
        )
        if len(hits) < 2:
            hits = hits + [
                h for h in mem.search(state.get("instruction") or "", k=3) if h not in hits
            ]
        return {**state, "memory": hits[:4]}
    except Exception as exc:  # noqa: BLE001
        logger.debug("retrieve skipped: %s", exc)
        return {**state, "memory": []}


def node_remember(state: AgentState) -> AgentState:
    try:
        chat_id = state.get("chat_id") or "global"
        instruction = (state.get("instruction") or "").strip()
        reply = (state.get("reply") or "").strip()
        mem = get_memory_store()
        if instruction:
            mem.add_turn(
                chat_id=chat_id,
                role="user",
                content=instruction,
                kind=state.get("intent") or "chat",
            )
        if reply:
            mem.add_turn(
                chat_id=chat_id,
                role="assistant",
                content=reply,
                kind=state.get("intent") or "chat",
            )
        for ch in state.get("changes") or []:
            if isinstance(ch, dict) and ch.get("product_name"):
                mem.add_turn(
                    chat_id=chat_id,
                    role="assistant",
                    content=f"Correction: {ch.get('product_name')}",
                    kind="correction",
                )
    except Exception as exc:  # noqa: BLE001
        logger.debug("remember skipped: %s", exc)
    return state


def node_route(state: AgentState) -> AgentState:
    products = state.get("products") or []
    intent = (
        "tender_fix"
        if looks_like_tender_task(state.get("instruction") or "", bool(products))
        else "chat"
    )
    return {**state, "intent": intent}


def node_chat(state: AgentState) -> AgentState:
    settings = get_settings()
    history = compact_history(state.get("history") or [], max_messages=8, max_chars_each=500)
    mem = state.get("memory") or []
    products = state.get("products") or []

    context_bits = []
    if mem:
        context_bits.append("Remembered snippets:\n- " + "\n- ".join(clip_text(m, 280) for m in mem))
    if products:
        context_bits.append(
            f"User has {len(products)} extracted tender products loaded "
            "(they can ask to fix names anytime)."
        )

    user_block = state.get("instruction") or ""
    lang_hint = (
        "Reply language: match the user's latest message exactly "
        "(English→English, Hindi→Hindi, Hinglish→Hinglish). Do not mix."
    )
    if context_bits:
        user_block = (
            "\n\n".join(context_bits)
            + "\n\n"
            + lang_hint
            + "\n\nUser: "
            + user_block
        )
    else:
        user_block = lang_hint + "\n\nUser: " + user_block

    messages = [{"role": "system", "content": _CHAT_SYSTEM}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_block})

    try:
        raw, provider = _call_llm(settings, messages, json_mode=False, temperature=0.55)
        return {**state, "reply": raw.strip(), "changes": [], "summary": "", "provider": provider}
    except Exception as exc:  # noqa: BLE001
        return {**state, "error": str(exc), "reply": "", "provider": "mistral"}


def node_tender_fix(state: AgentState) -> AgentState:
    settings = get_settings()
    history = compact_history(state.get("history") or [], max_messages=6, max_chars_each=400)
    products = state.get("products") or []
    mem = state.get("memory") or []

    prompt = (
        f"Rows:\n{json.dumps(products, ensure_ascii=False)}\n\n"
        f"User: {state.get('instruction') or ''}"
    )
    if mem:
        prompt = "Past corrections/memory:\n- " + "\n- ".join(clip_text(m, 200) for m in mem[:3]) + "\n\n" + prompt

    messages = [{"role": "system", "content": _FIX_SYSTEM}]
    messages.extend(history)
    messages.append({"role": "user", "content": prompt})

    try:
        raw, provider = _call_llm(settings, messages, json_mode=True, temperature=0.15)
        parsed = _parse_json(raw)
        return {
            **state,
            "reply": str(parsed.get("reply") or parsed.get("summary") or "Done.").strip(),
            "changes": list(parsed.get("changes") or []),
            "summary": str(parsed.get("summary") or "").strip(),
            "provider": provider,
        }
    except Exception as exc:  # noqa: BLE001
        return {**state, "error": str(exc), "reply": "", "provider": "mistral"}


def _branch(state: AgentState) -> Literal["chat", "tender_fix"]:
    return "tender_fix" if state.get("intent") == "tender_fix" else "chat"


def build_agent():
    g = StateGraph(AgentState)
    g.add_node("retrieve", node_retrieve)
    g.add_node("route", node_route)
    g.add_node("chat", node_chat)
    g.add_node("tender_fix", node_tender_fix)
    g.add_node("remember", node_remember)
    g.set_entry_point("retrieve")
    g.add_edge("retrieve", "route")
    g.add_conditional_edges("route", _branch, {"chat": "chat", "tender_fix": "tender_fix"})
    g.add_edge("chat", "remember")
    g.add_edge("tender_fix", "remember")
    g.add_edge("remember", END)
    return g.compile()


_AGENT = None


def get_agent():
    global _AGENT
    if _AGENT is None:
        _AGENT = build_agent()
    return _AGENT


def apply_product_changes(
    products: list[ProductItem],
    changes: list,
) -> tuple[int, int]:
    learning = get_name_learning()
    updated = removed = 0
    remove_indices: set[int] = set()
    for ch in changes:
        if not isinstance(ch, dict):
            continue
        idx = ch.get("index")
        if not isinstance(idx, int) or idx < 0 or idx >= len(products):
            continue
        if ch.get("remove"):
            desc = products[idx].description or ""
            if desc:
                learning.learn_reject(
                    desc,
                    reason="work" if is_work_description(desc) else "rejected",
                )
            remove_indices.add(idx)
            continue
        new_name = str(ch.get("product_name") or "").strip()
        desc = products[idx].description or ""
        checked = recheck_product_name(desc, new_name, extra_verbs=learning.extra_verbs)
        if not checked or not _validate_name(desc, checked):
            continue
        products[idx].product_name = checked
        learning.learn(desc, checked)
        updated += 1
    if remove_indices:
        products[:] = [p for i, p in enumerate(products) if i not in remove_indices]
        removed = len(remove_indices)
    if updated or removed:
        _renumber_sequential(products)
    return updated, removed


def run_agent(
    *,
    instruction: str,
    products: list[ProductItem] | None = None,
    history: list[dict[str, str]] | None = None,
    chat_id: str = "global",
) -> AgentState:
    products = products or []
    agent = get_agent()
    return agent.invoke({
        "instruction": instruction,
        "history": history or [],
        "products": _compact_rows(products),
        "chat_id": chat_id,
    })
