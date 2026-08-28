import asyncio
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import chromadb
from chromadb.config import Settings

from ..security.redact import redact_pii


COLLECTION_NAME = "mobile_agent_kb"
LOCAL_KB_PATH = Path(__file__).parent.parent.parent / "kb_data"


@dataclass
class ElementDoc:
    id: str                 # "{app_name}::{resource_id}::{class_name}"
    app_name: str
    element_sig: str        # "{resource_id}::{class_name}"
    class_name: str
    resource_id: str
    content_desc: str
    text: str
    documentation: str
    observed_result: str
    last_explored_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def _build_document(doc: ElementDoc) -> str:
    """Full text that gets embedded — includes identity + behavior for semantic transfer.

    `documentation`/`observed_result` are an LLM's free-text summary of what a
    screenshot showed — during Explore that's a real app (WhatsApp, Gmail, banking
    apps per SESSION_STATE.md), so a name/number/email visible on screen can end up
    quoted here. This is persisted to disk (ChromaDB) and later retrieved back into
    *other* sessions' prompts as docs_context — redact before it's ever written,
    not just at read time, so nothing sensitive is sitting on disk in the interim.
    """
    return (
        f"Element: {doc.class_name} | resource_id: {doc.resource_id} | "
        f"text: '{redact_pii(doc.text)}' | content_desc: '{redact_pii(doc.content_desc)}' | "
        f"Documentation: {redact_pii(doc.documentation)} "
        f"Observed result: {redact_pii(doc.observed_result)}"
    )


class KnowledgeBase:
    def __init__(
        self,
        app_name: str,
        chroma_host: Optional[str] = None,
        chroma_port: Optional[int] = None,
    ):
        self.app_name = app_name
        host = chroma_host or os.getenv("CHROMA_HOST")
        port = chroma_port or int(os.getenv("CHROMA_PORT", "8001"))

        if host:
            client = chromadb.HttpClient(host=host, port=port)
        else:
            LOCAL_KB_PATH.mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(
                path=str(LOCAL_KB_PATH),
                settings=Settings(anonymized_telemetry=False),
            )

        self._col = client.get_or_create_collection(
            COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

    async def upsert(self, doc: ElementDoc) -> None:
        await asyncio.to_thread(
            self._col.upsert,
            ids=[doc.id],
            documents=[_build_document(doc)],
            metadatas=[{
                "app_name": doc.app_name,
                "class_name": doc.class_name,
                "resource_id": doc.resource_id,
                "content_desc": doc.content_desc,
                "text": doc.text,
                "last_explored_at": doc.last_explored_at,
            }],
        )

    async def retrieve_context(self, elements: list) -> str:
        """Retrieve KB docs for visible elements and format as context string."""
        if not elements:
            return ""

        query_texts = [
            f"{e.class_name} {e.resource_id} {e.content_desc} {e.text}".strip()
            for e in elements
        ]

        try:
            results = await asyncio.to_thread(
                self._col.query,
                query_texts=query_texts,
                n_results=2,
                where={"app_name": self.app_name},
            )
        except Exception:
            return ""

        lines = []
        for i, elem in enumerate(elements):
            docs = results["documents"][i] if i < len(results["documents"]) else []
            if docs and docs[0]:
                lines.append(f"[Element {elem.id}]: {docs[0][:200]}")

        return "\n".join(lines) if lines else ""

    def get_all(self) -> list[ElementDoc]:
        try:
            res = self._col.get(where={"app_name": self.app_name})
        except Exception:
            return []

        docs = []
        for i, doc_id in enumerate(res["ids"]):
            meta = res["metadatas"][i]
            doc_text = res["documents"][i] if res["documents"] else ""
            docs.append(ElementDoc(
                id=doc_id,
                app_name=meta.get("app_name", ""),
                element_sig=f"{meta.get('resource_id','')}::{meta.get('class_name','')}",
                class_name=meta.get("class_name", ""),
                resource_id=meta.get("resource_id", ""),
                content_desc=meta.get("content_desc", ""),
                text=meta.get("text", ""),
                documentation=doc_text,
                observed_result="",
                last_explored_at=meta.get("last_explored_at", ""),
            ))
        return docs

    def count(self) -> int:
        try:
            return self._col.count()
        except Exception:
            return 0

    def clear(self) -> int:
        try:
            res = self._col.get(where={"app_name": self.app_name})
            ids = res["ids"]
            if ids:
                self._col.delete(ids=ids)
            return len(ids)
        except Exception:
            return 0
