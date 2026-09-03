"""LangGraph agent — chat + tender fixes, driven by saved user instructions."""

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
from app.services.ai.instruction_memory import add_instruction, format_for_prompt
from app.services.ai.memory_store import get_memory_store
from app.services.ai.mistral_client import chat_completion
from app.services.ai.token_budget import clip_text, compact_history, looks_like_tender_task
from app.services.name_learning import get_name_learning
from app.services.product_sanitize import _renumber_sequential

logger = logging.getLogger(__name__)

_CHAT_BASE = """You are Notofire AI — a capable assistant.
Help with general chat and Indian Railway / tender BOQ product extraction when relevant.
Match the language of the user's latest message (English / Hindi / Hinglish).
Do not ask to upload a PDF again if products are already loaded.
Follow SAVED USER INSTRUCTIONS when present — they override defaults.
Keep replies concise."""

_FIX_BASE = """You fix tender BOQ product rows.
Return ONLY valid JSON (no markdown fences):
{"reply":"short answer","changes":[{"index":0,"product_name":"...","remove":false}],"summary":"one line"}

Rules:
- Follow SAVED USER INSTRUCTIONS strictly for what counts as a product and how names look.
- product_name must use words from that row's description only — never invent.
- remove=true for rows that are not real products per user instructions.
- Include only rows that need a change; [] if none.
- Match reply language to the user."""

_DISTILL = """You extract durable rules from a user message for a tender product AI.
Return ONLY JSON: {"save":true,"rule":"..."} or {"save":false,"rule":""}
save=true only if the message teaches a reusable rule (naming style, what to exclude, etc.).
rule must be one clear English instruction. If greeting/one-off question → save=false."""


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


def _with_instructions(base: str) -> str:
    block = format_for_prompt()
    if not block:
        return base
    return f"{base}\n\n{block}"


def _compact_rows(products: list[ProductItem]) -> list[dict]:
    rows = []
    for i, p in enumerate(products):
        rows.append({
            "index": i,
            "s_no": p.s_no,
            "product_name": clip_text(p.product_name or "", 80),
            "description": clip_text(p.description or "", 140),
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
                    timeout=120,
                ),
                "mistral",
            )
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or not settings.gemini_api_key:
                raise
            provider = "gemini"

    system = ""
    parts = []
    for m in messages:
        if m["role"] == "system":
            system = m["content"]
        else:
            parts.append(f"{m['role']}: {m['content']}")
    payload = {
        "system_instruction": {"parts": [{"text": system or _CHAT_BASE}]},
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
        timeout=90,
        model_cache=[],
    )
    return raw, "gemini"


def capture_instruction_from_user(user_text: str, settings: Settings | None = None) -> None:
    """Distill a lasting rule from the user message and save it for future runs."""
    text = (user_text or "").strip()
    if len(text) < 20:
        return
    settings = settings or get_settings()
    if not settings.mistral_api_key and not settings.gemini_api_key:
        return
    try:
        raw, _ = _call_llm(
            settings,
            [
                {"role": "system", "content": _DISTILL},
                {"role": "user", "content": text[:2000]},
            ],
            json_mode=True,
            temperature=0.0,
        )
        parsed = _parse_json(raw)
        if parsed.get("save") and parsed.get("rule"):
            add_instruction(str(parsed["rule"]), source="distilled")
    except Exception as exc:  # noqa: BLE001
        logger.debug("instruction distill skipped: %s", exc)


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
        context_bits.append(
            "Remembered snippets:\n- " + "\n- ".join(clip_text(m, 280) for m in mem)
        )
    if products:
        context_bits.append(
            f"User has {len(products)} extracted tender products loaded."
        )

    user_block = state.get("instruction") or ""
    if context_bits:
        user_block = "\n\n".join(context_bits) + "\n\nUser: " + user_block
    else:
        user_block = "User: " + user_block

    messages = [{"role": "system", "content": _with_instructions(_CHAT_BASE)}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_block})

    try:
        raw, provider = _call_llm(settings, messages, json_mode=False, temperature=0.55)
        return {
            **state,
            "reply": raw.strip(),
            "changes": [],
            "summary": "",
            "provider": provider,
        }
    except Exception as exc:  # noqa: BLE001
        return {**state, "error": str(exc), "reply": "", "provider": "mistral"}


def node_tender_fix(state: AgentState) -> AgentState:
    settings = get_settings()
    history = compact_history(state.get("history") or [], max_messages=6, max_chars_each=400)
    products = state.get("products") or []
    mem = state.get("memory") or []
    instruction = state.get("instruction") or ""

    prompt = (
        f"Rows:\n{json.dumps(products, ensure_ascii=False)}\n\n"
        f"User: {instruction}"
    )
    if mem:
        prompt = (
            "Past memory:\n- "
            + "\n- ".join(clip_text(m, 200) for m in mem[:3])
            + "\n\n"
            + prompt
        )

    messages = [{"role": "system", "content": _with_instructions(_FIX_BASE)}]
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
    """Apply AI-proposed changes; names come from the model (instruction-driven)."""
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
                learning.learn_reject(desc, reason="rejected")
            remove_indices.add(idx)
            continue
        new_name = str(ch.get("product_name") or "").strip()
        desc = products[idx].description or ""
        if not _validate_name(desc, new_name):
            continue
        products[idx].product_name = new_name
        learning.learn(desc, new_name)
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
