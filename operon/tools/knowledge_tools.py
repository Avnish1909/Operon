"""Knowledge capability: search and read the company's SOPs and policies."""

from __future__ import annotations

from ..knowledge import KnowledgeBase
from .base import Tool, ToolError, ToolResult, schema


def knowledge_tools(kb: KnowledgeBase) -> list[Tool]:
    def search(args: dict) -> ToolResult:
        hits = kb.search(args["query"], k=int(args.get("limit", 4)))
        if not hits:
            return ToolResult("No matching company documents. Try other words, or read_document a catalog entry.")
        blocks = [f"[{h['doc_id']}] {h['title']} — section: {h['section']}\n{h['text']}" for h in hits]
        return ToolResult("\n\n---\n\n".join(blocks))

    def read(args: dict) -> ToolResult:
        try:
            doc = kb.read(args["doc_id"])
        except KeyError as exc:
            raise ToolError(str(exc)) from exc
        return ToolResult(f"[{doc.id}] {doc.title}\n\n{doc.body}")

    return [
        Tool("search_knowledge",
             "Search the company handbook (SOPs, policies, systems guide) for how something is done here. "
             "Use it before acting on any task, and whenever you are unsure about a rule, limit or procedure.",
             schema({"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]),
             search),
        Tool("read_document",
             "Read a full company document by its id (from the knowledge catalog or search results).",
             schema({"doc_id": {"type": "string"}}, ["doc_id"]),
             read),
    ]
