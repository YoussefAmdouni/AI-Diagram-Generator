import warnings
warnings.filterwarnings("ignore")

import os
import re
import asyncio
from typing import TypedDict, Literal

from langgraph.graph import StateGraph, END
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langgraph.checkpoint.memory import MemorySaver
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

tools    = [web_search_tool, mermaid_syntax_check]
tool_map = {
    "web_search_tool":      web_search_tool,
    "mermaid_syntax_check": mermaid_syntax_check,
}

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
    route:                str   # "workflow" | "direct" | "unsafe"
    max_iterations:       int
    final_answer:         str


# ─── Structured output models ─────────────────────────────────────────────────

class SafetyDecision(BaseModel):
    safe: bool = Field(
        description="True if the query is safe to process, False if it contains harmful content"
    )


class OrchestratorDecision(BaseModel):
    route: Literal["workflow", "direct"] = Field(
        description="Route the query to diagram workflow or answer directly"
    )


# ─── LLM clients ──────────────────────────────────────────────────────────────

_base_llm   = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.0)
_safety_llm = ChatGoogleGenerativeAI(model="gemma-3-27b-it",   temperature=0.0)

llm = _base_llm.with_retry(stop_after_attempt=3, wait_exponential_jitter=True)

structured_safety_llm = (
    _safety_llm
    .with_structured_output(SafetyDecision)
    .with_retry(stop_after_attempt=3, wait_exponential_jitter=True)
)

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
            elif isinstance(item, dict) and "content" in item:
                parts.append(str(item["content"]))
        return "".join(parts).strip()
    if isinstance(content, dict):
        return content.get("text", str(content)).strip()
    return str(content).strip()


async def run_tool_loop(llm_with_tools, initial_messages: list, max_iterations: int, node_name: str) -> str:
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

            tool_func   = tool_map.get(tool_name)
            tool_result = (
                await asyncio.to_thread(tool_func.invoke, tool_args)
                if tool_func
                else f"Tool '{tool_name}' not found"
            )

            tool_msg = {"role": "tool", "name": tool_name, "content": str(tool_result)}
            if tool_call_id:
                tool_msg["tool_call_id"] = tool_call_id
            messages.append(tool_msg)

    raise RuntimeError(f"{node_name} exceeded max iterations ({max_iterations})")


# ─── Nodes ────────────────────────────────────────────────────────────────────

async def safety_node(state: AgentState) -> AgentState:
    """
    First gate — uses Gemma-3-27b-it to decide if the query is safe.
    The regex sanitizer runs first as a fast, zero-latency pre-check.
    On any error the node fails open (treats query as safe) to avoid
    silently dropping legitimate requests.
    """
    task, flagged = sanitize_input(state["task"])

    if flagged:
        logger.warning(f"[SAFETY] Regex flagged input: {state['task'][:100]}")
        state["route"] = "unsafe"
        return state

    logger.info(f"[SAFETY] Checking with Gemma-3-27b-it: {state['task'][:100]}")

    safety_check_prompt = (
        f"{safety_prompt}\n\n"
        f"User query: {state['task']}\n\n"
        "Respond with your safety assessment."
    )

    try:
        decision = await structured_safety_llm.ainvoke(safety_check_prompt)
        if not decision.safe:
            logger.warning(f"[SAFETY] Gemma flagged query as unsafe: {state['task'][:100]}")
            state["route"] = "unsafe"
        else:
            logger.info("[SAFETY] Query passed — forwarding to orchestrator")
            state["route"] = ""   # cleared so orchestrator sets the real route
    except Exception as e:
        logger.warning(f"[SAFETY] Check errored, defaulting to safe: {e}")
        state["route"] = ""

    return state


async def orchestrator_node(state: AgentState) -> AgentState:
    """
    Routes safe queries to 'workflow' (Mermaid diagram) or 'direct' (general answer).
    Safety is fully handled by safety_node — this node never sets route='unsafe'.
    """
    logger.info(f"[ORCHESTRATOR] Routing query: {state['task'][:100]}")

    query_prompt = orchestrator_prompt.format(
        query=state["task"],
        conversation_context=state["conversation_context"],
    )

    try:
        response = await structured_llm.ainvoke(query_prompt)
        state["route"] = response.route if response.route in {"workflow", "direct"} else "direct"
    except Exception as e:
        logger.warning(f"[ORCHESTRATOR] Routing failed, defaulting to direct: {e}")
        state["route"] = "direct"

    logger.info(f"[ORCHESTRATOR] Route decision: {state['route']}")
    return state


SAFE_REFUSAL_MESSAGE = (
    "I can't help with that request. "
    "If you have another question or need help with a safe topic, I'm happy to help."
)


def unsafe_node(state: AgentState) -> AgentState:
    logger.warning(f"[UNSAFE NODE] Query blocked: {state['task'][:100]}")
    state["final_answer"] = SAFE_REFUSAL_MESSAGE
    return state


async def mermaid_node(state: AgentState) -> AgentState:
    prompt = mermaid_prompt.format(
        query=state["task"],
        conversation_context=state["conversation_context"],
    )
    try:
        state["final_answer"] = await run_tool_loop(
            llm_with_tools   = llm_mermaid,
            initial_messages = [{"role": "user", "content": prompt}],
            max_iterations   = state["max_iterations"],
            node_name        = "MERMAID",
        )
    except RuntimeError:
        state["final_answer"] = "I couldn't generate a valid diagram within the allowed attempts. Try simplifying your request."
    except Exception as e:
        logger.error(f"[MERMAID NODE] {e}", exc_info=True)
        state["final_answer"] = "I ran into an issue generating the diagram. Please try again."
    return state


async def direct_node(state: AgentState) -> AgentState:
    prompt = generale_purpose_prompt.format(
        query=state["task"],
        conversation_context=state["conversation_context"],
    )
    try:
        state["final_answer"] = await run_tool_loop(
            llm_with_tools   = llm_direct,
            initial_messages = [{"role": "user", "content": prompt}],
            max_iterations   = state["max_iterations"],
            node_name        = "DIRECT",
        )
    except RuntimeError:
        state["final_answer"] = "I couldn't retrieve the information. Please try again."
    except Exception as e:
        logger.error(f"[DIRECT NODE] {e}", exc_info=True)
        state["final_answer"] = "I ran into an issue. Please try again."
    return state


# ─── Graph ────────────────────────────────────────────────────────────────────

def _safety_route(state: AgentState) -> str:
    """After safety_node: short-circuit to unsafe, or forward to orchestrator."""
    if state.get("route") == "unsafe":
        logger.debug("Safety router → unsafe")
        return "unsafe"
    logger.debug("Safety router → orchestrator")
    return "orchestrator"


def _orchestrator_route(state: AgentState) -> str:
    """After orchestrator_node: direct or workflow."""
    route = state.get("route", "direct")
    logger.debug(f"Orchestrator router → {route}")
    return route


def build_sequential_graph():
    builder = StateGraph(AgentState)

    builder.add_node("safety",       safety_node)
    builder.add_node("orchestrator", orchestrator_node)
    builder.add_node("unsafe",       unsafe_node)
    builder.add_node("direct",       direct_node)
    builder.add_node("workflow",     mermaid_node)

    builder.set_entry_point("safety")

    builder.add_conditional_edges(
        "safety",
        _safety_route,
        {"unsafe": "unsafe", "orchestrator": "orchestrator"},
    )

    builder.add_conditional_edges(
        "orchestrator",
        _orchestrator_route,
        {"workflow": "workflow", "direct": "direct"},
    )

    builder.add_edge("workflow", END)
    builder.add_edge("direct",   END)
    builder.add_edge("unsafe",   END)

    return builder.compile()


# ─── Public API ───────────────────────────────────────────────────────────────

CONVERSATION_CONTEXT_LIMIT = int(os.getenv("CONVERSATION_CONTEXT_LIMIT", "10"))
MAX_ITERATIONS = int(os.getenv("AGENT_MAX_ITERATIONS", "5"))

_graph = build_sequential_graph()


def _format_conversation_context(conversation_history: list[dict]) -> str:
    if not conversation_history:
        return "No prior conversation context."

    formatted = "Prior conversation context:\n"
    for msg in conversation_history[-CONVERSATION_CONTEXT_LIMIT:]:
        content = msg.get("content", "")
        if any(p.search(content) for p in CONTEXT_INJECTION_PATTERNS):
            logger.warning(f"[CONTEXT] Suspicious content stripped: {content[:80]}")
            content = "[message removed]"
        msg_type = msg.get("type", "unknown").upper()
        formatted += f"{msg_type}: {content}\n"

    return formatted.strip()


async def get_response(user_message: str, conversation_history: list = None) -> str:
    """Main entry point called by FastAPI."""
    try:
        initial_state = AgentState(
            task=user_message,
            conversation_context=_format_conversation_context(conversation_history or []),
            route="",
            max_iterations=MAX_ITERATIONS,
            final_answer="",
        )
        result = await _graph.ainvoke(initial_state)
        return result.get("final_answer") or "I couldn't generate a response. Please try again."
    except Exception as e:
        logger.error(f"[get_response] {e}", exc_info=True)
        return f"Error processing your request: {str(e)}"