"""search_knowledge_base: the agent's knowledge base (12.9). The loader gives an agent this tool only when its
release lists knowledge items, and puts what it needs (store, project, items, embedder) in the executor's
`knowledge` — see runtime.platform.knowledge."""

from pydantic import BaseModel, Field

from runtime.tools.local import local_tool
from runtime.tools.types import ToolContext

NAME = "search_knowledge_base"


class SearchArgs(BaseModel):
    query: str = Field(description="What the caller wants to know, as a short question or keywords, in the caller's "
                                   "language (e.g. 'visiting hours', 'هل يوجد مواقف')")


@local_tool(
    NAME,
    "Search the knowledge base — the documents and notes this agent was given (services, policies, prices, opening "
    "hours, preparation instructions, FAQs …) — for facts that answer the caller's question. Use it before saying "
    "you don't know, and answer only from what it returns; if nothing relevant comes back, say you don't have that "
    "information.",
    SearchArgs,
)
async def search_knowledge_base(args: SearchArgs, ctx: ToolContext) -> dict:
    from runtime.platform.knowledge import search
    kb = ctx.extra.get("knowledge")
    if not kb or not kb.get("items"):
        return {"results": [], "note": "this agent has no knowledge base"}
    return await search(kb["store"], kb["workspace"], kb["items"], args.query, kb.get("embed"))
