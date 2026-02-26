import warnings
warnings.filterwarnings("ignore")

import os
import logging
from datetime import datetime
from langgraph.graph import StateGraph, END
from typing import TypedDict, List
import asyncio

from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from pydantic import BaseModel, Field

from langchain_openai import ChatOpenAI 
from langchain_google_genai import ChatGoogleGenerativeAI

from dotenv import load_dotenv
load_dotenv('.env')


# ============================================================================
# LOGGING SETUP
# ============================================================================

LOGS_DIR = "agent_logs"
if not os.path.exists(LOGS_DIR):
    os.makedirs(LOGS_DIR)

# Configure logging
log_filename = os.path.join(LOGS_DIR, f"agent_conversation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_filename, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
logger.info("=" * 80)
logger.info("Agent session started")
logger.info("=" * 80)


# ============================================================================
# OPENAI CLIENT SETUP
# ============================================================================
# llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash-lite", temperature=0.3)
llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.0)
# safety_llm = ChatOpenAI(base_url="http://localhost:1234/v1", api_key="not-needed")


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


def orchestrator_node(state: AgentState) -> AgentState:
    logger.info(f"[ORCHESTRATOR] Routing query: {state['task'][:100]}")

    query_prompt = orchestrator_prompt.format(
        query=state["task"],
        conversation_context=state["conversation_context"]
    )

    structured_llm = llm.with_structured_output(OrchestratorDecision)
    response = structured_llm.invoke(query_prompt)

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


def direct_node(state: AgentState) -> AgentState:
    """
    Answer using LLM knowledge (+ web if available).
    """

    logger.info(f"[DIRECT NODE] Answering query: {state['task'][:100]}")
    prompt = generale_purpose_prompt.format(
        query=state["task"],
        conversation_context=state["conversation_context"]
    )

    tool_enabled_llm = llm.bind_tools([web_search_tool])

    messages = [
        {"role": "user", "content": prompt}
    ]

    try:
        while True:
            response = tool_enabled_llm.invoke(messages)

            # If LLM gives final answer (no tool calls) → stop loop
            if not getattr(response, "tool_calls", None):
                # Extract text from response content
                state["final_answer"] = extract_text_content(response.content)
                return state

            # Execute each requested tool call
            for tool_call in response.tool_calls:
                tool_name = tool_call["name"]
                tool_args = tool_call["args"]
                tool_call_id = tool_call.get("id")

                logger.info(f"[DIRECT NODE] Tool requested: {tool_name}")

                # Get the actual tool function from the map
                tool_func = tool_map.get(tool_name)
                if tool_func:
                    tool_result = tool_func.invoke(tool_args)
                else:
                    tool_result = f"Tool {tool_name} not found"
                
                # Append assistant tool call message
                messages.append(response)

                # Append tool result so LLM can continue reasoning
                tool_result_msg = {
                    "role": "tool",
                    "name": tool_name,
                    "content": str(tool_result)
                }
                if tool_call_id:
                    tool_result_msg["tool_call_id"] = tool_call_id
                messages.append(tool_result_msg)

    except Exception as e:
        logger.error(f"[DIRECT NODE] Failure: {e}")
        state["final_answer"] = (
            "I ran into an issue while retrieving the information. "
            "Please try again."
        )
        return state


def mermaid_node(state: AgentState) -> AgentState:
    """
    Node to generate mermaid diagram based on user query.
    """
    logger.info(f"[MERMAID NODE] Generating diagram for: {state['task'][:100]}")
    prompt = generale_purpose_prompt.format(
        query=state["task"],
        conversation_context=state["conversation_context"]
    )

    tool_enabled_llm = llm.bind_tools([mermaid_syntax_check, web_search_tool])

    messages = [
        {"role": "user", "content": prompt}
    ]
    
    iteration = 0

    try:
        while iteration < state["max_iterations"]:
            iteration += 1
            logger.info(f"[MERMAID NODE] Iteration {iteration}")

            response = tool_enabled_llm.invoke(messages)

            # If LLM returns final answer (no tool call) → assume it's the valid code
            if not getattr(response, "tool_calls", None):
                # Extract text from response content
                state["final_answer"] = extract_text_content(response.content)
                return state

            # Execute tool calls
            for tool_call in response.tool_calls:
                tool_name = tool_call["name"]
                tool_args = tool_call["args"]
                tool_call_id = tool_call.get("id")

                logger.info(f"[MERMAID NODE] Tool requested: {tool_name}")

                # Get the actual tool function from the map
                tool_func = tool_map.get(tool_name)
                if tool_func:
                    tool_result = tool_func.invoke(tool_args)
                else:
                    tool_result = f"Tool {tool_name} not found"
                
                # Append assistant tool call message
                messages.append(response)

                # Append tool result
                tool_result_msg = {
                    "role": "tool",
                    "name": tool_name,
                    "content": str(tool_result),
                }
                if tool_call_id:
                    tool_result_msg["tool_call_id"] = tool_call_id
                messages.append(tool_result_msg)
        # Max iterations reached
        logger.error("[MERMAID NODE] Max iterations reached.")
        state["final_answer"] = (
            "I couldn’t generate a valid Mermaid diagram within the allowed attempts. "
            "Please try simplifying your request."
        )
        return state

    except Exception as e:
        logger.error(f"[MERMAID NODE] Failure: {e}")
        state["final_answer"] = (
            "I ran into an issue while generating the diagram. "
            "Please try again."
        )
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


def build_sequential_graph(checkpointer):
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

    # Compile with checkpointer
    graph = builder.compile(checkpointer=checkpointer)

    logger.info("Agent workflow graph compiled successfully")

    return graph    


# ============================================================================
# API FUNCTIONS FOR FASTAPI INTEGRATION
# ============================================================================

def _format_conversation_context(conversation_history: List[dict]) -> str:
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
    for msg in conversation_history[-10:]:  # Use last 10 messages for context
        msg_type = msg.get("type", "unknown").upper()
        content = msg.get("content", "")
        formatted += f"{msg_type}: {content}\n"
    
    return formatted.strip()


async def get_response(user_message: str, conversation_id: str, conversation_history: List[dict] = None) -> str:
    """
    Get an agent response for a user message.
    This is the main API function called by FastAPI endpoints.
    
    Args:
        user_message: The user's input message
        conversation_id: Unique identifier for conversation context
        conversation_history: Optional list of prior messages in the conversation
        
    Returns:
        str: The agent's response text
    """
    try:
        checkpointer = MemorySaver()
        graph = build_sequential_graph(checkpointer)
        
        # Create thread for conversation persistence
        thread = {"configurable": {"thread_id": conversation_id}}
        
        # Format conversation context from history
        context_str = _format_conversation_context(conversation_history or [])
        
        # Create initial state with prior conversation context
        initial_state = AgentState(
            task=user_message,
            conversation_context=context_str,
            route="",
            max_iterations=3,
            final_answer="",
        )
        
        # Invoke the graph
        result = await asyncio.to_thread(graph.invoke, initial_state, thread)
        
        response_text = ""
        if isinstance(result, dict):
            response_text = result.get("final_answer", "")
        else:
            response_text = getattr(result, "final_answer", "")

        if not response_text:
            response_text = (
                "I couldn’t generate a response at the moment. "
                "Please try again."
            )
        return response_text
        
    except Exception as e:
        logger.error(f"Error in get_response: {str(e)}", exc_info=True)
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
