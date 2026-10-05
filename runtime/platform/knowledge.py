"""Knowledge base (12.9): documents and free text an agent searches during a call.

Adding an item: its text is extracted (TXT / MD / HTML / PDF / DOCX / EPUB), split into ~800-character chunks on
paragraph and sentence boundaries, and each chunk embedded with the project's embedding connection (pgvector,
1024 dims). Chunks whose embedding fails keep a keyword index, so search still works — the item is then
"completed_with_errors".

Searching (the `search_knowledge_base` tool an agent gets when its release lists knowledge items): the question is
embedded and matched by vector similarity and by keywords, fused by reciprocal rank (`store.kb_search`). When the
embedding service is down the search is keyword-only.
"""

import asyncio
import html
import io
import logging
import re
import time
import zipfile
from html.parser import HTMLParser
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)

MAX_FILE_BYTES = 21 * 1024 * 1024          # per document (as Hamsa)
QUOTA_BYTES = 300 * 1024 * 1024            # per project
MAX_TEXT_CHARS = 25_000                    # free text items
EXTENSIONS = ("pdf", "docx", "txt", "md", "html", "htm", "epub")
CHUNK_CHARS, CHUNK_OVERLAP = 800, 120
EMBED_DIM = 1024
EMBED_BATCH = 16
SEARCH_EMBED_TIMEOUT_S = 3.0

Embedder = Callable[[list[str]], Awaitable[list[list[float]]]]


# ---------------------------------------------------------------- text extraction

class _Text(HTMLParser):
    SKIP = {"script", "style", "head", "noscript"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "table"}

    def __init__(self) -> None:
        super().__init__()
        self.out: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_text(markup: str) -> str:
    p = _Text()
    p.feed(markup)
    return html.unescape("".join(p.out))


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16", "cp1256", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def extract_text(filename: str, data: bytes) -> tuple[str, str]:
    """(text, extension) of a document; ValueError when the type isn't supported or nothing can be read."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == "doc":
        raise ValueError("old Word .doc files aren't supported — save it as .docx or PDF")
    if ext not in EXTENSIONS:
        raise ValueError(f"unsupported file type .{ext or '?'} — use PDF, DOCX, TXT, MD, HTML or EPUB")
    if ext in ("txt", "md"):
        text = _decode(data)
    elif ext in ("html", "htm"):
        text = html_text(_decode(data))
    elif ext == "pdf":
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                raise ValueError("the PDF is password-protected") from None
        text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
    elif ext == "docx":
        import docx
        d = docx.Document(io.BytesIO(data))
        parts = [p.text for p in d.paragraphs]
        for table in d.tables:
            for row in table.rows:
                parts.append(" | ".join(c.text.strip() for c in row.cells))
        text = "\n".join(parts)
    else:                                                  # epub: a zip of XHTML chapters
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = sorted(n for n in z.namelist() if n.lower().endswith((".xhtml", ".html", ".htm")))
            text = "\n\n".join(html_text(_decode(z.read(n))) for n in names)
        ext = "epub"
    text = normalize(text)
    if not text:
        raise ValueError("no text found in the file (a scanned PDF needs OCR first)")
    return text, "html" if ext == "htm" else ext


def normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def words(text: str) -> int:
    return len(text.split())


# ---------------------------------------------------------------- chunks

_SENTENCE = re.compile(r"(?<=[.!?؟。])\s+")


def chunk(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """~`size`-character chunks on paragraph / sentence boundaries; consecutive chunks share ~`overlap` chars."""
    pieces: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= size:
            pieces.append(para)
            continue
        for sent in _SENTENCE.split(para):
            while len(sent) > size:                      # a very long sentence: hard split
                pieces.append(sent[:size])
                sent = sent[size - overlap:]
            if sent.strip():
                pieces.append(sent.strip())
    chunks: list[str] = []
    cur = ""
    for p in pieces:
        if cur and len(cur) + 1 + len(p) > size:
            chunks.append(cur)
            tail = cur[-overlap:]
            cur = (tail[tail.find(" ") + 1:] if " " in tail else tail) + " " + p
        else:
            cur = f"{cur}\n{p}" if cur else p
    if cur.strip():
        chunks.append(cur)
    return chunks


# ---------------------------------------------------------------- ingest / search

async def ingest(store, item_id: str, ws: str, text: str, embed: Embedder | None) -> dict[str, Any]:
    """Chunk + embed `text` into the item's chunks and finish its status. Never raises (status says what failed)."""
    started = time.monotonic()
    try:
        parts = chunk(text)
        vectors: list[list[float] | None] = [None] * len(parts)
        failed = 0
        if embed is not None:
            for i in range(0, len(parts), EMBED_BATCH):
                batch = parts[i:i + EMBED_BATCH]
                try:
                    got = await embed(batch)
                    for j, v in enumerate(got):
                        vectors[i + j] = v if v and len(v) == EMBED_DIM else None
                        failed += vectors[i + j] is None
                except Exception as e:                    # noqa: BLE001 — keyword search still works
                    failed += len(batch)
                    log.warning("knowledge %s: embedding failed for chunks %d-%d: %r", item_id, i, i + len(batch), e)
        else:
            failed = len(parts)
        await store.set_kb_chunks(item_id, ws, [(n, t, vectors[n]) for n, t in enumerate(parts)])
        status = "completed" if not failed else "completed_with_errors"
        error = None if not failed else (f"{failed} of {len(parts)} parts have no semantic index (embedding service "
                                         "unavailable) — keyword search only for them")
        await store.put_kb_item({"id": item_id, "status": status, "error": error, "chunks": len(parts),
                                 "words": words(text)})
        return {"status": status, "chunks": len(parts), "ms": round((time.monotonic() - started) * 1000)}
    except Exception as e:                               # noqa: BLE001
        log.exception("knowledge %s: ingest failed", item_id)
        await store.put_kb_item({"id": item_id, "status": "failed", "error": str(e)[:500]})
        return {"status": "failed", "error": str(e)}


async def search(store, ws: str, item_ids: list[str], query: str, embed: Embedder | None, k: int = 4) -> dict:
    query = (query or "").strip()
    if not query or not item_ids:
        return {"results": [], "note": "nothing to search"}
    vector = None
    mode = "keyword"
    if embed is not None:
        try:
            got = await asyncio.wait_for(embed([query]), SEARCH_EMBED_TIMEOUT_S)
            if got and len(got[0]) == EMBED_DIM:
                vector, mode = got[0], "hybrid"
        except Exception as e:                           # noqa: BLE001
            log.warning("knowledge search: embedding unavailable, keyword only: %r", e)
    rows = await store.kb_search(ws, item_ids, query, vector, k)
    return {"mode": mode, "results": [{"source": r["name"], "text": r["text"]} for r in rows]}


def embedder_from(provider) -> Embedder | None:
    """An embedding provider (runtime.providers) as an Embedder, or None."""
    if provider is None:
        return None

    async def embed(texts: list[str]) -> list[list[float]]:
        return await provider.embed(texts)
    return embed


__all__ = ["EXTENSIONS", "MAX_FILE_BYTES", "MAX_TEXT_CHARS", "QUOTA_BYTES", "chunk", "embedder_from",
           "extract_text", "ingest", "normalize", "search", "words"]
