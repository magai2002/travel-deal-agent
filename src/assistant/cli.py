"""
Interactive terminal loop for the config/price-history assistant.
Run with: python -m src.assistant.cli
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from src.agent.cost import CostTracker
from src.assistant.graph import build_graph

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[RotatingFileHandler(LOG_DIR / "assistant.log", maxBytes=5_000_000, backupCount=5)],
)
log = logging.getLogger("travel-assistant")


def _extract_text(content) -> str:
    """
    AIMessage.content is a plain string for most models, but with extended
    thinking (e.g. claude-sonnet-5) it's a list of blocks — thinking blocks
    (with a long opaque signature) plus text blocks. Only the text blocks
    are meant for the user.
    """
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        block_type = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
        if block_type == "text":
            parts.append(block["text"] if isinstance(block, dict) else block.text)
    return "\n".join(parts)


def _handle_result(result: dict, graph, config: dict) -> None:
    if "__interrupt__" in result:
        interrupt_obj = result["__interrupt__"][0]
        payload = interrupt_obj.value
        print("\n[Confirmation needed]")
        for call in payload.get("proposed_calls", []):
            print(f"  {call['tool']}({call['args']})")
        answer = input("Approve? (y/n): ").strip().lower()
        decision = {"approved": answer == "y"}
        if answer != "y":
            reason = input("Reason (optional, Enter to skip): ").strip()
            if reason:
                decision["reason"] = reason
        result = graph.invoke(Command(resume=decision), config)
        _handle_result(result, graph, config)
        return

    last_message = result["messages"][-1]
    print(f"\nAssistant: {_extract_text(last_message.content)}\n")


def main() -> None:
    db_path = os.environ.get("ASSISTANT_STATE_DB", "assistant_state.db")
    thread_id = os.environ.get("ASSISTANT_THREAD_ID", "default")
    cost_tracker = CostTracker()
    config = {"configurable": {"thread_id": thread_id, "cost_tracker": cost_tracker}}

    with SqliteSaver.from_conn_string(db_path) as checkpointer:
        graph = build_graph(checkpointer)
        print("Travel agent assistant. Type 'exit' to quit.\n")
        while True:
            user_input = input("You: ").strip()
            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit"):
                break
            result = graph.invoke(
                {"messages": [{"role": "user", "content": user_input}]}, config
            )
            _handle_result(result, graph, config)
            log.info(cost_tracker.summary())

    log.info("Session ended — %s", cost_tracker.summary())


if __name__ == "__main__":
    main()
