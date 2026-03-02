import warnings
warnings.filterwarnings("ignore")

import os
import re
import asyncio
from typing import TypedDict, Literal, AsyncIterator

from langgraph.graph import StateGraph, END
from pydantic import BaseModel, Field
from langchain_google_genai import ChatGoogleGenerativeAI

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


# ─── Structured output models ─────────────────────────────────────────────────
class SafetyDecision(BaseModel):
    safe: bool = Field(description="True if safe, False if harmful")

class OrchestratorDecision(BaseModel):
    route: Literal["workflow", "direct"] = Field(
        description="Route to Mermaid diagram workflow or answer directly"
    )


# ─── LLM clients ──────────────────────────────────────────────────────────────
_base_llm   = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.0)
_safety_llm = ChatGoogleGenerativeAI(model="gemma-3-27b-it",   temperature=0.0)

structured_safety_llm = (
    _safety_llm.with_structured_output(SafetyDecision)
    .with_retry(stop_after_attempt=3, wait_exponential_jitter=True)
)
structured_llm = (
    _base_llm.with_structured_output(OrchestratorDecision)
    .with_retry(stop_after_attempt=3, wait_exponential_jitter=True)
)
llm_direct = (
    _base_llm.bind_tools([web_search_tool])
    .with_retry(stop_after_attempt=3, wait_exponential_jitter=True)
)
llm_mermaid = (
    _base_llm.bind_tools([mermaid_syntax_check, web_search_tool])
    .with_retry(stop_after_attempt=3, wait_exponential_jitter=True)
)


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
    """Non-streaming tool loop used by mermaid_node (needs full response for validation)."""
    messages  = list(initial_messages)
    iteration = 0

    while iteration < max_iterations:
        iteration += 1
        logger.info(f"[{node_name}] Iteration {iteration}/{max_iterations}")
        response = await llm_with_tools.ainvoke(messages)

        if not getattr(response, "tool_calls", None):
            return extract_text_content(response.content)

        messages.append(response)
        for tool_call in response.tool_calls:
            tool_name    = tool_call["name"]
            tool_args    = tool_call["args"]
            tool_call_id = tool_call.get("id")
            tool_func    = tool_map.get(tool_name)
            tool_result  = (
                await asyncio.to_thread(tool_func.invoke, tool_args)
                if tool_func else f"Tool '{tool_name}' not found"
            )
            tool_msg = {"role": "tool", "name": tool_name, "content": str(tool_result)}
            if tool_call_id:
                tool_msg["tool_call_id"] = tool_call_id
            messages.append(tool_msg)

    raise RuntimeError(f"{node_name} exceeded max iterations ({max_iterations})")


async def stream_tool_loop(
    llm_with_tools,
    initial_messages: list,
    max_iterations: int,
    node_name: str,
) -> AsyncIterator[str]:
    """
    Streaming tool loop — yields text chunks as they arrive from the LLM.
    Tool calls are resolved silently (no streaming needed for tool output).
    """
    messages  = list(initial_messages)
    iteration = 0

    while iteration < max_iterations:
        iteration += 1
        logger.info(f"[{node_name}] Stream iteration {iteration}/{max_iterations}")

        # Accumulate the streamed response to check for tool calls
        collected_chunks  = []
        collected_content = []
        tool_calls_buffer = {}  # id -> {name, args_str}
        has_tool_calls    = False

        async for chunk in llm_with_tools.astream(messages):
            # Accumulate tool call fragments
            if chunk.tool_call_chunks:
                has_tool_calls = True
                for tc_chunk in chunk.tool_call_chunks:
                    tc_id = tc_chunk.get("id") or tc_chunk.get("index", "0")
                    if tc_id not in tool_calls_buffer:
                        tool_calls_buffer[tc_id] = {
                            "id":       tc_chunk.get("id", ""),
                            "name":     tc_chunk.get("name", ""),
                            "args_str": "",
                        }
                    tool_calls_buffer[tc_id]["args_str"] += tc_chunk.get("args", "")

            # Stream text content to caller
            text = extract_text_content(chunk.content)
            if text:
                collected_content.append(text)
                yield text

            collected_chunks.append(chunk)

        if not has_tool_calls:
            return  # done — no more tool calls

        # Resolve tool calls
        import json
        full_response = collected_chunks[-1] if collected_chunks else None
        if full_response:
            messages.append(full_response)

        for tc_id, tc in tool_calls_buffer.items():
            try:
                args = json.loads(tc["args_str"]) if tc["args_str"] else {}
            except json.JSONDecodeError:
                args = {}

            tool_func   = tool_map.get(tc["name"])
            tool_result = (
                await asyncio.to_thread(tool_func.invoke, args)
                if tool_func else f"Tool '{tc['name']}' not found"
            )
            tool_msg = {"role": "tool", "name": tc["name"], "content": str(tool_result)}
            if tc.get("id"):
                tool_msg["tool_call_id"] = tc["id"]
            messages.append(tool_msg)

    raise RuntimeError(f"{node_name} exceeded max stream iterations ({max_iterations})")


# ─── Nodes ────────────────────────────────────────────────────────────────────
async def safety_node(state: AgentState) -> AgentState:
    task, flagged = sanitize_input(state["task"])
    if flagged:
        logger.warning(f"[SAFETY] Regex flagged: {state['task'][:100]}")
        state["route"] = "unsafe"
        return state

    logger.info(f"[SAFETY] Gemma check: {state['task'][:100]}")
    try:
        decision = await structured_safety_llm.ainvoke(
            f"{safety_prompt}\n\nUser query: {state['task']}\n\nRespond with your assessment."
        )
        state["route"] = "" if decision.safe else "unsafe"
        if not decision.safe:
            logger.warning(f"[SAFETY] Gemma flagged: {state['task'][:100]}")
    except Exception as e:
        logger.warning(f"[SAFETY] Check error, defaulting safe: {e}")
        state["route"] = ""
    return state


async def orchestrator_node(state: AgentState) -> AgentState:
    logger.info(f"[ORCHESTRATOR] Routing: {state['task'][:100]}")
    try:
        response = await structured_llm.ainvoke(
            orchestrator_prompt.format(
                query=state["task"],
                conversation_context=state["conversation_context"],
            )
        )
        state["route"] = response.route if response.route in {"workflow", "direct"} else "direct"
    except Exception as e:
        logger.warning(f"[ORCHESTRATOR] Failed, defaulting direct: {e}")
        state["route"] = "direct"
    logger.info(f"[ORCHESTRATOR] → {state['route']}")
    return state


SAFE_REFUSAL = (
    "I can't help with that request. "
    "If you have another question or need help with a safe topic, I'm happy to help."
)


def unsafe_node(state: AgentState) -> AgentState:
    logger.warning(f"[UNSAFE] Blocked: {state['task'][:100]}")
    state["final_answer"] = SAFE_REFUSAL
    return state


async def mermaid_node(state: AgentState) -> AgentState:
    """Uses non-streaming loop — mermaid needs full response for syntax validation."""
    try:
        state["final_answer"] = await run_tool_loop(
            llm_with_tools   = llm_mermaid,
            initial_messages = [{"role": "user", "content": mermaid_prompt.format(
                query=state["task"],
                conversation_context=state["conversation_context"],
            )}],
            max_iterations   = state["max_iterations"],
            node_name        = "MERMAID",
        )
    except RuntimeError:
        state["final_answer"] = "Couldn't generate a valid diagram. Try simplifying your request."
    except Exception as e:
        logger.error(f"[MERMAID] {e}", exc_info=True)
        state["final_answer"] = "Error generating diagram. Please try again."
    return state


async def direct_node(state: AgentState) -> AgentState:
    """Non-streaming fallback — result stored for SSE streaming in main.py."""
    try:
        state["final_answer"] = await run_tool_loop(
            llm_with_tools   = llm_direct,
            initial_messages = [{"role": "user", "content": generale_purpose_prompt.format(
                query=state["task"],
                conversation_context=state["conversation_context"],
            )}],
            max_iterations   = state["max_iterations"],
            node_name        = "DIRECT",
        )
    except RuntimeError:
        state["final_answer"] = "Couldn't retrieve information. Please try again."
    except Exception as e:
        logger.error(f"[DIRECT] {e}", exc_info=True)
        state["final_answer"] = "An error occurred. Please try again."
    return state


# ─── Graph ────────────────────────────────────────────────────────────────────
def _safety_route(state: AgentState) -> str:
    return "unsafe" if state.get("route") == "unsafe" else "orchestrator"

def _orchestrator_route(state: AgentState) -> str:
    return state.get("route", "direct")


def build_graph():
    builder = StateGraph(AgentState)
    builder.add_node("safety",       safety_node)
    builder.add_node("orchestrator", orchestrator_node)
    builder.add_node("unsafe",       unsafe_node)
    builder.add_node("direct",       direct_node)
    builder.add_node("workflow",     mermaid_node)

    builder.set_entry_point("safety")
    builder.add_conditional_edges("safety",       _safety_route,       {"unsafe": "unsafe", "orchestrator": "orchestrator"})
    builder.add_conditional_edges("orchestrator", _orchestrator_route, {"workflow": "workflow", "direct": "direct"})
    builder.add_edge("workflow", END)
    builder.add_edge("direct",   END)
    builder.add_edge("unsafe",   END)
    return builder.compile()


# ─── Public API ───────────────────────────────────────────────────────────────
CONVERSATION_CONTEXT_LIMIT = int(os.getenv("CONVERSATION_CONTEXT_LIMIT", "10"))
MAX_ITERATIONS             = int(os.getenv("AGENT_MAX_ITERATIONS", "5"))

_graph = build_graph()


def _format_conversation_context(history: list[dict]) -> str:
    if not history:
        return "No prior conversation context."
    formatted = "Prior conversation context:\n"
    for msg in history[-CONVERSATION_CONTEXT_LIMIT:]:
        content = msg.get("content", "")
        if any(p.search(content) for p in CONTEXT_INJECTION_PATTERNS):
            logger.warning(f"[CONTEXT] Stripped suspicious content: {content[:80]}")
            content = "[message removed]"
        formatted += f"{msg.get('type', 'unknown').upper()}: {content}\n"
    return formatted.strip()


async def get_response(user_message: str, conversation_history: list = None) -> str:
    """Non-streaming entry point (used internally by streaming generator)."""
    initial_state = AgentState(
        task=user_message,
        conversation_context=_format_conversation_context(conversation_history or []),
        route="",
        max_iterations=MAX_ITERATIONS,
        final_answer="",
    )
    result = await _graph.ainvoke(initial_state)
    return result.get("final_answer") or "I couldn't generate a response. Please try again."


async def stream_response(
    user_message: str,
    conversation_history: list = None,
) -> AsyncIterator[str]:
    """
    Streaming entry point for SSE.
    Runs safety + orchestrator (non-streaming, fast), then streams the final answer.
    Yields text chunks. Yields a special '__DONE__' sentinel when complete.
    """
    context = _format_conversation_context(conversation_history or [])

    # ── Phase 1: safety check (fast, no streaming needed) ──
    task, flagged = sanitize_input(user_message)
    if flagged:
        yield SAFE_REFUSAL
        yield "__DONE__"
        return

    try:
        safety_decision = await structured_safety_llm.ainvoke(
            f"{safety_prompt}\n\nUser query: {user_message}\n\nRespond with your assessment."
        )
        if not safety_decision.safe:
            yield SAFE_REFUSAL
            yield "__DONE__"
            return
    except Exception as e:
        logger.warning(f"[STREAM] Safety check error, defaulting safe: {e}")

    # ── Phase 2: orchestrator routing (fast) ──
    try:
        orch_decision = await structured_llm.ainvoke(
            orchestrator_prompt.format(query=user_message, conversation_context=context)
        )
        route = orch_decision.route if orch_decision.route in {"workflow", "direct"} else "direct"
    except Exception as e:
        logger.warning(f"[STREAM] Orchestrator error, defaulting direct: {e}")
        route = "direct"

    logger.info(f"[STREAM] Route → {route}")

    # ── Phase 3: stream the actual answer ──
    full_response_parts = []

    if route == "workflow":
        # Mermaid needs validation loop — can't stream mid-validation
        # So we run it fully then stream the result word by word for UX consistency
        try:
            full = await run_tool_loop(
                llm_with_tools   = llm_mermaid,
                initial_messages = [{"role": "user", "content": mermaid_prompt.format(
                    query=user_message, conversation_context=context
                )}],
                max_iterations   = MAX_ITERATIONS,
                node_name        = "MERMAID_STREAM",
            )
            # Stream word by word so frontend feels responsive
            for word in full.split(" "):
                yield word + " "
                await asyncio.sleep(0.01)
            full_response_parts.append(full)
        except Exception as e:
            logger.error(f"[STREAM MERMAID] {e}", exc_info=True)
            err = "Error generating diagram. Please try again."
            yield err
            full_response_parts.append(err)
    else:
        # Direct — true streaming from Gemini
        try:
            async for chunk in stream_tool_loop(
                llm_with_tools   = llm_direct,
                initial_messages = [{"role": "user", "content": generale_purpose_prompt.format(
                    query=user_message, conversation_context=context
                )}],
                max_iterations   = MAX_ITERATIONS,
                node_name        = "DIRECT_STREAM",
            ):
                full_response_parts.append(chunk)
                yield chunk
        except Exception as e:
            logger.error(f"[STREAM DIRECT] {e}", exc_info=True)
            err = "An error occurred. Please try again."
            yield err
            full_response_parts.append(err)

    yield "__DONE__"