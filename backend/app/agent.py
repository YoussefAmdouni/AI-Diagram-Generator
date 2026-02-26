import warnings
warnings.filterwarnings("ignore")

import os
import logging
import json
from datetime import datetime, timezone
from langgraph.graph import StateGraph, END
from typing import TypedDict, List
import asyncio

from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from pydantic import BaseModel, Field
from context import request_id_var

from langchain_openai import ChatOpenAI 
from langchain_google_genai import ChatGoogleGenerativeAI

from dotenv import load_dotenv
load_dotenv('.env')


# ============================================================================
# LOGGING SETUP
# ============================================================================

class JSONFormatter(logging.Formatter):
    """Emit each log record as a single JSON line."""
    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level":     record.levelname,
            "logger":    record.name,
            "message":   record.getMessage(),
            "module":    record.module,
            "line":      record.lineno,
            "request_id": request_id_var.get(),
        }
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        # Include any extra fields passed via extra={} in log calls
        for key, val in record.__dict__.items():
            if key not in {
                "msg", "args", "levelname", "levelno", "pathname", "filename",
                "module", "exc_info", "exc_text", "stack_info", "lineno",
                "funcName", "created", "msecs", "relativeCreated", "thread",
                "threadName", "processName", "process", "name", "message",
            }:
                log_obj[key] = val
        return json.dumps(log_obj)


LOGS_DIR = "agent_logs"
os.makedirs(LOGS_DIR, exist_ok=True)

# Rotating: max 10MB per file, keep 5 backups → max 50MB total log storage
rotating_handler = logging.handlers.RotatingFileHandler(
    filename=os.path.join(LOGS_DIR, "agent.log"),   # fixed name, not timestamped
    maxBytes=10 * 1024 * 1024,                       # 10MB
    backupCount=5,
    encoding="utf-8",
)
rotating_handler.setFormatter(JSONFormatter())

console_handler = logging.StreamHandler()
console_handler.setFormatter(JSONFormatter())

logging.basicConfig(level=logging.INFO, handlers=[rotating_handler, console_handler])
logger = logging.getLogger(__name__)
logger.info("Agent session started")

# ============================================================================
# PROMPT SETUP
# ============================================================================
import yaml
with open("prompt.yaml", "r", encoding="utf-8") as f:
    prompt_data = yaml.safe_load(f)

mermaid_prompt = prompt_data["mermaid_prompt"]
safety_prompt = prompt_data["user_check_safety_prompt"]
orchestrator_prompt = prompt_data["orchestrator_prompt"]
generale_purpose_prompt = prompt_data["generale_purpose_prompt"]

#`============================================================================
# TOOL SETUP
# ============================================================================`
from tool import web_search_tool, mermaid_syntax_check 
tools = [web_search_tool, mermaid_syntax_check]

# Create tool map for lookup by name
tool_map = {
    "web_search_tool": web_search_tool,
    "mermaid_syntax_check": mermaid_syntax_check
}

# ============================================================================
# SAFETY CHECKER FUNCTION
# ============================================================================
import re 

MAX_INPUT_LENGTH = 2000  # already have 8000 in main.py 

# Patterns that are almost never legitimate in a diagram tool
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

def sanitize_input(text: str) -> tuple[str, bool]:
    """
    Returns (sanitized_text, was_flagged).
    Does not block — flags for logging and strips known injection scaffolding.
    Hard blocks only on pattern matches.
    """
    if len(text) > MAX_INPUT_LENGTH:
        text = text[:MAX_INPUT_LENGTH]

    for pattern in COMPILED_PATTERNS:
        if pattern.search(text):
            return text, True   # flag it — let unsafe_node handle response

    return text, False

CONTEXT_INJECTION_PATTERNS = [
    re.compile(r"(ignore|disregard|forget).{0,30}(instruction|rule|prompt)", re.IGNORECASE),
    re.compile(r"you are (now|a|an|DAN)", re.IGNORECASE),
    re.compile(r"system prompt", re.IGNORECASE),
]

# ============================================================================
# STATE & MODELS
# ============================================================================

class AgentState(TypedDict):
    """State for the escalation agent workflow."""
    task: str  # User query
    conversation_context: str  # Context from previous interactions
    route: str  # "workflow" or "direct" or "unsafe"
    iteration_count: int # Number of iterations
    max_iterations: int # Max iterations before drafting response
    final_answer: str # Final answer to return to user

# ============================================================================
# DATA MODELS
# ============================================================================
from typing import Literal

class OrchestratorDecision(BaseModel):
    route: Literal["workflow", "direct", "unsafe"] = Field(
        description="Whether to route the query to the escalation workflow or answer directly or flag as unsafe"
    )

# ============================================================================
# OPENAI CLIENT SETUP
# ============================================================================
# llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash-lite", temperature=0.3)
# llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.0)
# safety_llm = ChatOpenAI(base_url="http://localhost:1234/v1", api_key="not-needed")
_base_llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.0)

llm = _base_llm.with_retry(
    stop_after_attempt=3,
    wait_exponential_jitter=True,   
)

structured_llm = _base_llm.with_structured_output(OrchestratorDecision).with_retry(
    stop_after_attempt=3,
    wait_exponential_jitter=True,
)

llm_direct = _base_llm.bind_tools([web_search_tool]).with_retry(
    stop_after_attempt=3,
    wait_exponential_jitter=True,
)

llm_mermaid = _base_llm.bind_tools([mermaid_syntax_check, web_search_tool]).with_retry(
    stop_after_attempt=3,
    wait_exponential_jitter=True,
)
# ============================================================================
# NODE FUNCTIONS
# ============================================================================

def extract_text_content(content) -> str:
    """
    Extract plain text from various response content formats.
    Handles strings, lists of strings, and structured content objects.
    """
    if isinstance(content, str):
        return content.strip()
    
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and 'text' in item:
                parts.append(item['text'])
            elif isinstance(item, dict):
                # If it's a dict but no 'text' key, try to get string representation
                if 'content' in item:
                    parts.append(str(item['content']))
        return "".join(parts).strip()
    
    if isinstance(content, dict):
        if 'text' in content:
            return content['text'].strip()
        return str(content).strip()
    
    return str(content).strip()


async def orchestrator_node(state: AgentState) -> AgentState:
    task, flagged = sanitize_input(state["task"])

    if flagged:
        logger.warning(f"[ORCHESTRATOR] Input flagged by sanitizer: {state['task'][:100]}")
        state["route"] = "unsafe"
        return state
    
    logger.info(f"[ORCHESTRATOR] Routing query: {state['task'][:100]}")

    query_prompt = orchestrator_prompt.format(
        query=state["task"],
        conversation_context=state["conversation_context"]
    )

    response = await structured_llm.ainvoke(query_prompt)

    try:
        if response.route in {"unsafe", "workflow", "direct"}:
            state["route"] = response.route
        else:
            state["route"] = "direct"

        logger.info(f"[OK] Route Decision: {state['route']}")

    except Exception as e:
        logger.warning(f"Structured routing failed: {e}")
        state["route"] = "direct"

    return state

SAFE_REFUSAL_MESSAGE = (
    "I can’t help with that request. "
    "If you have another question or need help with a safe topic, I’m happy to help."
)

def unsafe_node(state: AgentState) -> AgentState:
    """
    Handle unsafe queries (crime, prompt injection, jailbreak attempts, etc.)
    Returns a safe refusal.
    """
    logger.warning(f"[UNSAFE NODE] Query blocked: {state['task'][:100]}")

    state["final_answer"] = SAFE_REFUSAL_MESSAGE
    return state

async def run_tool_loop(llm_with_tools, initial_messages: list, max_iterations: int, node_name: str) -> str:
    messages  = list(initial_messages)
    iteration = 0

    while iteration < max_iterations:
        iteration += 1
        logger.info(f"[{node_name}] Iteration {iteration}/{max_iterations}")

        response = await llm_with_tools.ainvoke(messages)   # async LLM call

        if not getattr(response, "tool_calls", None):
            return extract_text_content(response.content)

        messages.append(response)
        for tool_call in response.tool_calls:
            tool_name    = tool_call["name"]
            tool_args    = tool_call["args"]
            tool_call_id = tool_call.get("id")

            tool_func   = tool_map.get(tool_name)
            # run sync tools in thread so they don't block the event loop
            if tool_func:
                tool_result = await asyncio.to_thread(tool_func.invoke, tool_args)
            else:
                tool_result = f"Tool '{tool_name}' not found"

            tool_msg = {"role": "tool", "name": tool_name, "content": str(tool_result)}
            if tool_call_id:
                tool_msg["tool_call_id"] = tool_call_id
            messages.append(tool_msg)

    raise RuntimeError(f"{node_name} exceeded max iterations")


async def mermaid_node(state: AgentState) -> AgentState:
    prompt = mermaid_prompt.format(
        query=state["task"],
        conversation_context=state["conversation_context"]
    )
    try:
        state["final_answer"] = await run_tool_loop(
            llm_with_tools   = llm_mermaid,
            initial_messages = [{"role": "user", "content": prompt}],
            max_iterations   = state["max_iterations"],
            node_name        = "MERMAID",
        )
    except RuntimeError as e:
        state["final_answer"] = "I couldn't generate a valid diagram within the allowed attempts. Try simplifying your request."
    except Exception as e:
        logger.error(f"[MERMAID NODE] {e}", exc_info=True)
        state["final_answer"] = "I ran into an issue generating the diagram. Please try again."
    return state


async def direct_node(state: AgentState) -> AgentState:
    prompt = generale_purpose_prompt.format(
        query=state["task"],
        conversation_context=state["conversation_context"]
    )
    try:
        state["final_answer"] = await run_tool_loop(
            llm_with_tools   = llm_direct,
            initial_messages = [{"role": "user", "content": prompt}],
            max_iterations   = state["max_iterations"],
            node_name        = "DIRECT",
        )
    except RuntimeError as e:
        state["final_answer"] = "I couldn't retrieve the information. Please try again."
    except Exception as e:
        logger.error(f"[DIRECT NODE] {e}", exc_info=True)
        state["final_answer"] = "I ran into an issue. Please try again."
    return state

# ============================================================================
# GRAPH CONSTRUCTION
# ============================================================================

def should_run_workflow(state: AgentState) -> str:
    """
    Conditional router: Decide whether to run full workflow or return direct or unsafe answer.
    
    This router is invoked after the orchestrator node and determines the path
    through the agent workflow. 
    
    Args:
        state: The current agent state containing the route decision
        
    Returns:
        str: Either "workflow" (to intake node) or "communication" (direct path)
    """
    route = state.get("route", "")
    
    if route == "workflow":
        logger.debug("Router: Directing to diagram generation workflow")
        return "workflow"
    
    elif route == "unsafe":
        logger.debug("Router: Directing to unsafe node")
        return "unsafe"
    
    else:
        logger.debug("Router: Directing to direct communication response")
        return "direct"


def build_sequential_graph():
    """
    Build and compile the multi-agent workflow graph.
    """

    builder = StateGraph(AgentState)

    # Add nodes
    builder.add_node("orchestrator", orchestrator_node)
    builder.add_node("unsafe", unsafe_node)
    builder.add_node("direct", direct_node)
    builder.add_node("workflow", mermaid_node)

    # Entry point
    builder.set_entry_point("orchestrator")

    # Conditional routing after orchestrator
    builder.add_conditional_edges(
        "orchestrator",
        should_run_workflow,  # router function
        {
            "workflow": "workflow",
            "direct": "direct",
            "unsafe": "unsafe",
        },
    )

    # All terminal nodes → END
    builder.add_edge("workflow", END)
    builder.add_edge("direct", END)
    builder.add_edge("unsafe", END)


    return builder.compile()  


# ============================================================================
# API FUNCTIONS FOR FASTAPI INTEGRATION
# ============================================================================

def _format_conversation_context(conversation_history: list[dict]) -> str:
    """
    Format conversation history into a readable context string for the agent.
    
    Args:
        conversation_history: List of conversation messages with type, content, timestamp
        
    Returns:
        str: Formatted conversation context for the prompts
    """
    if not conversation_history:
        return "No prior conversation context."

    formatted = "Prior conversation context:\n"
    for msg in conversation_history[-10:]:
        content = msg.get("content", "")

        # Sanitize stored messages before re-injecting them into new prompts
        is_suspicious = any(p.search(content) for p in CONTEXT_INJECTION_PATTERNS)
        if is_suspicious:
            logger.warning(f"[CONTEXT] Suspicious content stripped from context: {content[:80]}")
            content = "[message removed]"

        msg_type = msg.get("type", "unknown").upper()
        formatted += f"{msg_type}: {content}\n"

    return formatted.strip()

MAX_ITERATIONS = int(os.getenv("AGENT_MAX_ITERATIONS", "5")) 

async def get_response(user_message: str, conversation_history: list = None) -> str:
    """
    Get an agent response for a user message.
    This is the main API function called by FastAPI endpoints.
    
    Args:
        user_message: The user's input message
        conversation_history: Optional list of prior messages in the conversation
        
    Returns:
        str: The agent's response text
    """
    try:
        graph         = build_sequential_graph()
        context_str   = _format_conversation_context(conversation_history or [])
        initial_state = AgentState(
            task=user_message,
            conversation_context=context_str,
            route="",
            max_iterations=MAX_ITERATIONS,
            final_answer="",
        )

        # ainvoke keeps everything on the event loop — no thread blocking
        result = await graph.ainvoke(initial_state)
        return result.get("final_answer") or "I couldn't generate a response. Please try again."

    except Exception as e:
        logger.error(f"[get_response] {e}", exc_info=True)
        return f"Error processing your request: {str(e)}"



async def get_conversation_messages(conversation_id: str) -> list:
    """
    Get conversation history for a conversation ID.
    This retrieves stored messages from the conversation persistence layer.
    
    Args:
        conversation_id: Unique identifier for the conversation
        
    Returns:
        list: List of message dictionaries with 'type', 'content', and 'timestamp'
    """
    try:
        # For now, return empty list as the conversation is managed by SessionManager in main.py
        # The actual messages are stored in SessionManager's session memory
        logger.info(f"[{conversation_id}] Retrieved conversation history")
        return []
        
    except Exception as e:
        logger.error(f"Error in get_conversation_messages: {str(e)}", exc_info=True)
        return []
