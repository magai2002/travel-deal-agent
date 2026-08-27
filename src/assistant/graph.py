"""
LangGraph state graph for the conversational config/price-history assistant.

Idiomatic patterns used deliberately, since learning them properly (not
just "an LLM call in a graph-shaped costume") is the point of building this
piece on LangGraph at all:

  - typed state with the built-in `add_messages` reducer, so conversation
    history accumulates correctly across turns and across process restarts
    when combined with a persistent checkpointer
  - conditional routing based on WHICH tools were called (read-only vs
    mutating), not just whether any tool was called at all
  - `interrupt()` / `Command(resume=...)` for human-in-the-loop approval —
    this pauses the *entire graph run*, not just a Python function call,
    and — because the checkpointer persists state — the pause survives
    even if the process exits and is restarted later (see cli.py, which
    uses SqliteSaver rather than an in-memory checkpointer for exactly
    this reason)
  - the prebuilt `ToolNode` for actually executing tool calls, rather than
    hand-rolling dispatch logic the framework already provides

GOTCHA, documented here because it's the single most common way this
pattern breaks in practice: when a graph resumes after an interrupt,
LangGraph re-runs the ENTIRE node containing the interrupt() call from its
start — not from the exact line the interrupt happened on. `human_review`
below calls `interrupt()` as the very first thing it does, with zero side
effects before it, specifically so a resume can't double up on work.
"""
from __future__ import annotations

import os
from typing import Annotated, Literal

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import ToolMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.types import interrupt
from typing_extensions import TypedDict

from src.assistant.tools import ALL_TOOLS, MUTATING_TOOL_NAMES

MODEL_NAME = os.environ.get("ASSISTANT_MODEL", "claude-sonnet-5")

SYSTEM_PROMPT = """You are Alexey's travel-deal assistant. You help him \
manage which routes are tracked (add/remove/edit routes, thresholds, and \
home base) and answer questions about recent prices from stored history.

Be concise. When proposing a config change, call the appropriate tool \
directly rather than asking permission in words first — the system will \
handle getting his confirmation before anything is actually saved. If a \
change is rejected, don't retry it silently; ask what he'd like instead."""


class AssistantState(TypedDict):
    messages: Annotated[list, add_messages]


def _agent_node(state: AssistantState) -> dict:
    model = ChatAnthropic(model=MODEL_NAME).bind_tools(ALL_TOOLS)
    messages = state["messages"]
    if not messages or messages[0].type != "system":
        from langchain_core.messages import SystemMessage
        messages = [SystemMessage(content=SYSTEM_PROMPT)] + messages
    response = model.invoke(messages)
    return {"messages": [response]}


def _route_after_agent(state: AssistantState) -> Literal["human_review", "tools", "__end__"]:
    last = state["messages"][-1]
    tool_calls = getattr(last, "tool_calls", None)
    if not tool_calls:
        return END
    if any(call["name"] in MUTATING_TOOL_NAMES for call in tool_calls):
        return "human_review"
    return "tools"


def _human_review_node(state: AssistantState) -> dict:
    last = state["messages"][-1]
    mutating_calls = [c for c in last.tool_calls if c["name"] in MUTATING_TOOL_NAMES]

    # Must be the first thing this node does — see module docstring GOTCHA.
    decision = interrupt(
        {
            "type": "confirm_action",
            "proposed_calls": [
                {"tool": c["name"], "args": c["args"]} for c in mutating_calls
            ],
        }
    )

    if decision.get("approved"):
        # Nothing to do here — falling through lets routing send this to
        # the `tools` node next, which will execute every tool_call on the
        # last message (mutating ones included) via the prebuilt ToolNode.
        return {}

    # Rejected: every tool_call on an AIMessage requires a matching
    # ToolMessage response or the conversation is malformed from the
    # model's perspective. Synthesize rejections and skip `tools` entirely.
    rejection_reason = decision.get("reason", "User did not approve this action.")
    rejections = [
        ToolMessage(content=rejection_reason, tool_call_id=c["id"])
        for c in mutating_calls
    ]
    return {"messages": rejections}


def _route_after_review(state: AssistantState) -> Literal["tools", "agent"]:
    # If human_review already synthesized rejection ToolMessages, the last
    # message is now a ToolMessage — skip execution and let the agent
    # respond to the human. Otherwise it was approved; go execute.
    if isinstance(state["messages"][-1], ToolMessage):
        return "agent"
    return "tools"


def build_graph(checkpointer):
    graph = StateGraph(AssistantState)

    graph.add_node("agent", _agent_node)
    graph.add_node("tools", ToolNode(ALL_TOOLS))
    graph.add_node("human_review", _human_review_node)

    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", _route_after_agent, ["human_review", "tools", END])
    graph.add_conditional_edges("human_review", _route_after_review, ["tools", "agent"])
    graph.add_edge("tools", "agent")

    return graph.compile(checkpointer=checkpointer)
