"""LangGraph agent: multi-hop root cause analysis with tool-calling."""

import json
import logging
from typing import Annotated, Any, TypedDict

import duckdb
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from sherlogs.config import MAX_HOPS, MODEL_NAME
from sherlogs.loader import IncidentCase
from sherlogs.pipeline import build_tables
from sherlogs.prompt import SYSTEM_PROMPT_AGENT
from sherlogs.tools import create_tools
from sherlogs.types import Verdict
from sherlogs.utils.prompt_builder import build_prompt

logger = logging.getLogger(__name__)


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    case: IncidentCase
    con: duckdb.DuckDBPyConnection
    tools: list[BaseTool]
    hops_remaining: int
    verdict: Verdict | None


def pipeline_node(state: AgentState) -> dict[str, Any]:
    """Build DuckDB tables, create tools, set up initial messages."""
    case = state["case"]
    con = build_tables(case)
    tools = create_tools(con, case.inject_time)

    template_dump = build_prompt(con, case)

    messages = [
        SystemMessage(content=SYSTEM_PROMPT_AGENT),
        HumanMessage(
            content=f"{template_dump}\n\n"
            f"Investigate this incident. Use your tools to drill deeper, follow error chains, "
            f"and gather evidence. You have {MAX_HOPS} tool calls available."
        ),
    ]

    return {
        "con": con,
        "tools": tools,
        "messages": messages,
        "hops_remaining": MAX_HOPS,
    }


def reason_node(state: AgentState) -> dict[str, Any]:
    """Call Gemini with tools, return updated messages and hop count."""
    llm = ChatGoogleGenerativeAI(model=MODEL_NAME)
    llm_with_tools = llm.bind_tools(state["tools"])

    response = llm_with_tools.invoke(state["messages"])

    hops = state["hops_remaining"]
    if response.tool_calls:
        hops -= len(response.tool_calls)

    return {"messages": [response], "hops_remaining": hops}


def should_continue(state: AgentState) -> str:
    """Route after reason: continue tool-calling, force answer, or finish."""
    last_message = state["messages"][-1]

    if not isinstance(last_message, AIMessage):
        return END

    if last_message.tool_calls and state["hops_remaining"] > 0:
        return "tools"

    if last_message.tool_calls and state["hops_remaining"] <= 0:
        return "force_answer"

    return END


def force_answer_node(state: AgentState) -> dict[str, Any]:
    """Called when hops are exhausted. Strip pending tool calls, ask LLM for final JSON verdict."""
    llm = ChatGoogleGenerativeAI(model=MODEL_NAME)
    force_msg = HumanMessage(
        content="You have run out of tool calls. Based on everything you have seen so far, "
        "give your final answer now as JSON: "
        '{"service": "name", "confidence": "high|medium|low", "top3": ["s1", "s2", "s3"], "reasoning": "..."}'
    )
    messages = state["messages"] + [force_msg]
    response = llm.invoke(messages)
    return {"messages": [force_msg, response]}


def _extract_text(content: str | list[str | dict[str, str]]) -> str:
    """Extract plain text from LLM response content (handles both str and list formats)."""
    if isinstance(content, str):
        return content
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            return block.get("text", "")
    return ""


def extract_verdict(state: AgentState) -> dict[str, Any]:
    """Parse the final LLM message into a Verdict."""
    last_message = state["messages"][-1]
    text = _extract_text(last_message.content)

    try:
        clean = text.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        verdict: Verdict = json.loads(clean)
        return {"verdict": verdict}
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        logger.warning("Failed to parse verdict: %s", e)

    return {
        "verdict": Verdict(
            service="unknown",
            confidence="low",
            top3=["unknown"],
            reasoning=f"Parse error. Raw: {text[:500]}",
        )
    }


def tool_node(state: AgentState) -> dict[str, list[BaseMessage]]:
    """Execute tool calls from the last AI message using tools bound in state."""
    node = ToolNode(tools=state["tools"])
    result: dict[str, list[BaseMessage]] = node.invoke(state)
    hops = state["hops_remaining"]
    for msg in result.get("messages", []):
        msg.content = f"{msg.content}\n\n[{hops} tool calls remaining]"
    return result


def build_graph() -> StateGraph[AgentState]:
    graph: StateGraph[AgentState] = StateGraph(AgentState)

    graph.add_node("pipeline", pipeline_node)
    graph.add_node("reason", reason_node)
    graph.add_node("tools", tool_node)
    graph.add_node("force_answer", force_answer_node)
    graph.add_node("extract", extract_verdict)

    graph.set_entry_point("pipeline")
    graph.add_edge("pipeline", "reason")
    graph.add_conditional_edges(
        "reason",
        should_continue,
        {"tools": "tools", "force_answer": "force_answer", END: "extract"},
    )
    graph.add_edge("tools", "reason")
    graph.add_edge("force_answer", "extract")
    graph.add_edge("extract", END)

    return graph


def run_agent(case: IncidentCase, verbose: bool = False) -> Verdict:
    """Run the full agent on one incident case."""
    graph = build_graph()
    app = graph.compile()

    initial_state: AgentState = {
        "messages": [],
        "case": case,
        "con": duckdb.connect(),
        "tools": [],
        "hops_remaining": MAX_HOPS,
        "verdict": None,
    }

    final_state: dict[str, Any] = {}
    for step in app.stream(initial_state, stream_mode="updates"):
        for node_name, updates in step.items():
            if verbose and node_name == "reason":
                messages = updates.get("messages", [])
                if messages:
                    last = messages[-1]
                    if isinstance(last, AIMessage):
                        if last.tool_calls:
                            for tc in last.tool_calls:
                                logger.info("  -> %s(%s)", tc["name"], tc["args"])
                        elif last.content:
                            content = (
                                last.content
                                if isinstance(last.content, str)
                                else str(last.content)
                            )
                            logger.info("  Final answer: %s", content[:200])
            if node_name == "tools" and verbose:
                messages = updates.get("messages", [])
                for msg in messages:
                    content = (
                        msg.content
                        if isinstance(msg.content, str)
                        else str(msg.content)
                    )
                    logger.info("  <- %s", content[:300])
            final_state.update(updates)

    con = final_state.get("con")
    if con:
        con.close()

    verdict = final_state.get("verdict")
    if verdict is None:
        return Verdict(
            service="unknown",
            confidence="low",
            top3=["unknown"],
            reasoning="No verdict produced",
        )

    if verbose:
        logger.info("  Verdict: %s (%s)", verdict["service"], verdict["confidence"])
        logger.info("  Top3: %s", verdict["top3"])

    return verdict
