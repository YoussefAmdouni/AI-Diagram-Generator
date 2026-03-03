import warnings
warnings.filterwarnings("ignore")

import os
import re
import asyncio
from typing import TypedDict, Literal, AsyncIterator

from pydantic import BaseModel, Field
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage

from dotenv import load_dotenv
load_dotenv('.env')

from logger import get_logger
logger = get_logger(__name__)
logger.info("Agent session started")

import yaml
with open("prompt.yaml", "r", encoding="utf-8") as f:
    prompt_data = yaml.safe_load(f)

mermaid_prompt          = prompt_data["mermaid_prompt"]
safety_prompt           = prompt_data["user_check_safety_prompt"]
orchestrator_prompt     = prompt_data["orchestrator_prompt"]
generale_purpose_prompt = prompt_data["generale_purpose_prompt"]

from tool import web_search_tool, mermaid_syntax_check

tool_map = {
    "web_search_tool":      web_search_tool,
    "mermaid_syntax_check": mermaid_syntax_check,
}

# ─── Input sanitisation ───────────────────────────────────────────────────────
MAX_INPUT_LENGTH = 8000

INJECTION_PATTERNS = [
    r"ignore (previous|prior|above|all) instructions",
    r"you are now",
    r"act as (if you are|a|an)",
    r"forget (your|all) (instructions|rules|constraints)",
    r"repeat (the|your) (system )?prompt",
    r"disregard (your|the) (previous|prior|system)",
    r"jailbreak",
    r"dan mode",
]
COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]

CONTEXT_INJECTION_PATTERNS = [
    re.compile(r"(ignore|disregard|forget).{0,30}(instruction|rule|prompt)", re.IGNORECASE),
    re.compile(r"you are (now|a|an|DAN)", re.IGNORECASE),
    re.compile(r"system prompt", re.IGNORECASE),
]


def sanitize_input(text: str) -> tuple[str, bool]:
    if len(text) > MAX_INPUT_LENGTH:
        text = text[:MAX_INPUT_LENGTH]
    for pattern in COMPILED_PATTERNS:
        if pattern.search(text):
            return text, True
    return text, False


# ─── State ────────────────────────────────────────────────────────────────────
class AgentState(TypedDict):
    task:                 str
    conversation_context: str
    route:                str
    max_iterations:       int
    final_answer:         str


# ─── Structured output model ──────────────────────────────────────────────────
class OrchestratorDecision(BaseModel):
    route: Literal["workflow", "direct"] = Field(
        description="Route to Mermaid diagram workflow or answer directly"
    )


# ─── LLM clients ──────────────────────────────────────────────────────────────
_base_llm   = ChatGoogleGenerativeAI(model="gemini-2.5-flash",      temperature=0.0)
_safety_llm = ChatGoogleGenerativeAI(model="gemini-2.0-flash-lite", temperature=0.0)

structured_llm = (
    _base_llm
    .with_structured_output(OrchestratorDecision)
    .with_retry(stop_after_attempt=3, wait_exponential_jitter=True)
)

llm_direct = (
    _base_llm
    .bind_tools([web_search_tool])
    .with_retry(stop_after_attempt=3, wait_exponential_jitter=True)
)

llm_mermaid = (
    _base_llm
    .bind_tools([mermaid_syntax_check, web_search_tool])
    .with_retry(stop_after_attempt=3, wait_exponential_jitter=True)
)


# ─── Safety check (flash-lite, plain text) ────────────────────────────────────
_SAFETY_SYSTEM = (
    "You are a safety classifier. Respond with exactly one word.\n"
    "Reply 'unsafe' if the query contains: violent or hateful content, "
    "sexually explicit material, self-harm promotion, or private/sensitive personal data.\n"
    "Reply 'safe' for everything else.\n"
    "One word only. No explanation."
)


async def _check_safety(query: str) -> bool:
    """Returns True if safe, False if unsafe. Fails open on error."""
    try:
        response = await _safety_llm.ainvoke([
            HumanMessage(content=f"{_SAFETY_SYSTEM}\n\nUser query: {query}")
        ])
        text    = response.content.strip().lower() if isinstance(response.content, str) else "safe"
        is_safe = "unsafe" not in text
        logger.info(f"[SAFETY] flash-lite: '{text}' → {'safe' if is_safe else 'UNSAFE'}")
        return is_safe
    except Exception as e:
        logger.warning(f"[SAFETY] Check failed, defaulting safe: {e}")
        return True


# ─── Helpers ──────────────────────────────────────────────────────────────────
def extract_text_content(content) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and "text" in item:
                parts.append(item["text"])
        return "".join(parts).strip()
    if isinstance(content, dict):
        return content.get("text", str(content)).strip()
    return str(content).strip()


async def run_tool_loop(
    llm_with_tools,
    initial_messages: list,
    max_iterations: int,
    node_name: str,
) -> str:
    """
    Runs the LLM + tool loop to completion, returns the full text answer.
    Used for both direct (web search) and mermaid (syntax validation) workflows.
    """
    messages  = list(initial_messages)
    iteration = 0

    while iteration < max_iterations:
        iteration += 1
        logger.info(f"[{node_name}] Iteration {iteration}/{max_iterations}")

        response = await llm_with_tools.ainvoke(messages)

        # No tool calls — final answer reached
        if not getattr(response, "tool_calls", None):
            return extract_text_content(response.content)

        # Resolve all tool calls then loop
        messages.append(response)
        for tool_call in response.tool_calls:
            tool_name    = tool_call["name"]
            tool_args    = tool_call["args"]
            tool_call_id = tool_call.get("id")
            tool_func    = tool_map.get(tool_name)

            logger.info(f"[{node_name}] Tool call: {tool_name}")
            tool_result = (
                await asyncio.to_thread(tool_func.invoke, tool_args)
                if tool_func else f"Tool '{tool_name}' not found"
            )
            tool_msg = {"role": "tool", "name": tool_name, "content": str(tool_result)}
            if tool_call_id:
                tool_msg["tool_call_id"] = tool_call_id
            messages.append(tool_msg)

    raise RuntimeError(f"{node_name} exceeded max iterations ({max_iterations})")


# ─── Constants ────────────────────────────────────────────────────────────────
SAFE_REFUSAL = (
    "I can't help with that request. "
    "If you have another question or need help with a safe topic, I'm happy to help."
)

CONVERSATION_CONTEXT_LIMIT = int(os.getenv("CONVERSATION_CONTEXT_LIMIT", "10"))
MAX_ITERATIONS             = int(os.getenv("AGENT_MAX_ITERATIONS", "5"))


def _format_conversation_context(history: list[dict]) -> str:
    if not history:
        return "No prior conversation context."
    formatted = "Prior conversation context:\n"
    for msg in history[-CONVERSATION_CONTEXT_LIMIT:]:
        content = msg.get("content", "")
        if any(p.search(content) for p in CONTEXT_INJECTION_PATTERNS):
            logger.warning(f"[CONTEXT] Stripped: {content[:80]}")
            content = "[message removed]"
        formatted += f"{msg.get('type', 'unknown').upper()}: {content}\n"
    return formatted.strip()


# ─── Main entry point ─────────────────────────────────────────────────────────
async def stream_response(
    user_message: str,
    conversation_history: list = None,
) -> AsyncIterator[str]:
    """
    Full pipeline — yields exactly two things:
      1. The complete answer as a single chunk  (or SAFE_REFUSAL)
      2. '__DONE__' sentinel

    SSE value: decouples the HTTP connection from LLM latency.
    No word-by-word simulation — the full answer arrives once the tool
    loop completes, which is the right behaviour for both mermaid and direct.
    """
    context = _format_conversation_context(conversation_history or [])

    # ── 1. Regex sanitizer (instant) ──
    _, flagged = sanitize_input(user_message)
    if flagged:
        logger.warning(f"[SAFETY] Regex flagged: {user_message[:100]}")
        yield SAFE_REFUSAL
        yield "__DONE__"
        return

    # ── 2. Flash-lite safety check ──
    if not await _check_safety(user_message):
        yield SAFE_REFUSAL
        yield "__DONE__"
        return

    # ── 3. Orchestrator routing ──
    try:
        decision = await structured_llm.ainvoke(
            orchestrator_prompt.format(
                query=user_message,
                conversation_context=context,
            )
        )
        route = decision.route if decision.route in {"workflow", "direct"} else "direct"
    except Exception as e:
        logger.warning(f"[STREAM] Orchestrator error, defaulting direct: {e}")
        route = "direct"

    logger.info(f"[STREAM] Route → {route}")

    # ── 4. Run tool loop to completion ──
    try:
        if route == "workflow":
            answer = await run_tool_loop(
                llm_with_tools   = llm_mermaid,
                initial_messages = [{"role": "user", "content": mermaid_prompt.format(
                    query=user_message, conversation_context=context,
                )}],
                max_iterations   = MAX_ITERATIONS,
                node_name        = "MERMAID",
            )
        else:
            answer = await run_tool_loop(
                llm_with_tools   = llm_direct,
                initial_messages = [{"role": "user", "content": generale_purpose_prompt.format(
                    query=user_message, conversation_context=context,
                )}],
                max_iterations   = MAX_ITERATIONS,
                node_name        = "DIRECT",
            )
    except RuntimeError as e:
        logger.error(f"[STREAM] Tool loop exhausted: {e}")
        answer = "I couldn't complete the request within the allowed steps. Please try again."
    except Exception as e:
        logger.error(f"[STREAM] Unexpected error: {e}", exc_info=True)
        answer = "An error occurred. Please try again."

    # ── 5. Yield full answer then sentinel ──
    yield answer
    yield "__DONE__"


# ─── Non-streaming wrapper (kept for compatibility) ───────────────────────────
async def get_response(user_message: str, conversation_history: list = None) -> str:
    parts = []
    async for chunk in stream_response(user_message, conversation_history):
        if chunk != "__DONE__":
            parts.append(chunk)
    return "".join(parts) or "I couldn't generate a response. Please try again."