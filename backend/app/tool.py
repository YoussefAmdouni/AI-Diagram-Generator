from dotenv import load_dotenv
load_dotenv('.env')

from langchain_community.tools.tavily_search import TavilySearchResults
from langchain_core.tools import tool
import subprocess
import tempfile
import os
import shutil
import re
from agent import logger

tavily = TavilySearchResults(max_results=2)

@tool
def web_search_tool(query: str) -> str:
    """
    Search the web for up-to-date information.
    Use this when the user asks about current events, recent data,
    or information not in the model's knowledge.
    Args:
        query (str): The search query.
    Returns:
        str: The search results.
    """
    results = tavily.invoke({"query": query})

    # Format results into clean text for the LLM
    formatted = []
    for r in results:
        title = r.get("title", "")
        content = r.get("content", "")
        url = r.get("url", "")
        formatted.append(f"{title}\n{content}\nSource: {url}")

    return "\n\n".join(formatted)


def clean_mermaid_error(error: str) -> str:
    """
    Removes file paths like:
    - C:\\Users\\...
    - file:///C:/Users/...
    while preserving actual parser error details.
    """
    if not error:
        return error

    # Remove file:///C:/Users/... or file:///D:/...
    error = re.sub(
        r"file:///+[A-Za-z]:/[^\s\n]+",
        "path)",
        error
    )

    # Remove Windows paths like C:\Users\...
    error = re.sub(
        r"[A-Za-z]:\\[^\s\n]+",
        "path)",
        error
    )

    # Clean up extra spaces / empty parentheses
    error = re.sub(r"\(\s*\)", "", error)
    error = re.sub(r"\s{2,}", " ", error)

    return error.strip()

@tool
def mermaid_syntax_check(mermaid_code: str) -> dict:
    """
    Check Mermaid syntax using Mermaid CLI.
    Args:
        mermaid_code (str): The Mermaid diagram code to validate.
    Returns:
        dict: {"valid": bool, "error": str or None}
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
            logger.warning("[mermaid_syntax_check] mmdc timed out after 15s")
            return {"valid": False, "error": "Mermaid CLI timed out. Try a simpler diagram."}
        except FileNotFoundError:
            return {"valid": False, "error": f"Could not execute mmdc at path: {mmdc}"}

        if result.returncode != 0:
            return {"valid": False, "error": clean_mermaid_error(result.stderr)}

        return {"valid": True, "error": None}