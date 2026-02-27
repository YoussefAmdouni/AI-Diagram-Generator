from dotenv import load_dotenv
load_dotenv('.env')

import os
import re
import shutil
import subprocess
import tempfile

from langchain_community.tools.tavily_search import TavilySearchResults
from langchain_core.tools import tool

# ─── Shared logger ────────────────────────────────────────────────────────────

from logger import get_logger
logger = get_logger(__name__)

# ─── Tavily search ────────────────────────────────────────────────────────────
tavily = TavilySearchResults(max_results=2)


@tool
def web_search_tool(query: str) -> str:
    """
    Search the web for up-to-date information.
    Use this when the user asks about current events, recent data,
    or information not in the model's knowledge.

    Args:
        query: The search query.
    Returns:
        Formatted search results as plain text.
    """
    results = tavily.invoke({"query": query})
    formatted = []
    for r in results:
        title   = r.get("title", "")
        content = r.get("content", "")
        url     = r.get("url", "")
        formatted.append(f"{title}\n{content}\nSource: {url}")
    return "\n\n".join(formatted)


# ─── Mermaid syntax check ─────────────────────────────────────────────────────

def _clean_mermaid_error(error: str) -> str:
    """Strip local file-system paths from mmdc error output."""
    if not error:
        return error
    error = re.sub(r"file:///+[A-Za-z]:/[^\s\n]+", "path)", error)
    error = re.sub(r"[A-Za-z]:\\[^\s\n]+",          "path)", error)
    error = re.sub(r"\(\s*\)", "",                   error)
    error = re.sub(r"\s{2,}", " ",                   error)
    return error.strip()


@tool
def mermaid_syntax_check(mermaid_code: str) -> dict:
    """
    Validate Mermaid diagram syntax using the Mermaid CLI (mmdc).

    Args:
        mermaid_code: The Mermaid diagram source to validate.
    Returns:
        {"valid": bool, "error": str | None}
    """
    mmdc = shutil.which("mmdc") or shutil.which("mmdc.cmd")
    if not mmdc:
        return {"valid": False, "error": "Mermaid CLI (mmdc) not found in PATH"}

    with tempfile.TemporaryDirectory() as tmpdir:
        mmd_file = os.path.join(tmpdir, "diagram.mmd")
        out_file = os.path.join(tmpdir, "out.svg")

        with open(mmd_file, "w", encoding="utf-8") as f:
            f.write(mermaid_code)

        try:
            result = subprocess.run(
                [mmdc, "-i", mmd_file, "-o", out_file],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=15,
            )
        except subprocess.TimeoutExpired:
            logger.warning("[mermaid_syntax_check] mmdc timed out after 15 s")
            return {"valid": False, "error": "Mermaid CLI timed out. Try a simpler diagram."}
        except FileNotFoundError:
            return {"valid": False, "error": f"Could not execute mmdc at path: {mmdc}"}

        if result.returncode != 0:
            return {"valid": False, "error": _clean_mermaid_error(result.stderr)}

        return {"valid": True, "error": None}