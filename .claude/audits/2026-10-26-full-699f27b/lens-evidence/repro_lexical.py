# Simulates two gateway workers sharing one document + vector store (as Postgres/Qdrant are shared).
import asyncio, sys, logging
sys.path.insert(0, sys.argv[1])
logging.disable(logging.CRITICAL)
from aura_core.container import build_container
from aura_core.config import load_settings
from aura_core.knowledge.engine import KnowledgeEngine
from aura_core.domain.identifiers import UserId

async def main():
    c = build_container(load_settings())
    a = c.knowledge
    b = KnowledgeEngine(documents=a._documents, vectors=a._vectors, embedder=a._embedder,
                        index_manager=a._indexes, top_k=5)
    await c.index_manager.ensure_ready()
    u = UserId("u1")
    # worker B starts (rebuilds empty lexical index), then worker A ingests
    await b.rebuild_lexical_index()
    r = await a.ingest(content="The AURA-4419 escalation runbook says to page the on-call SRE.", title="runbook", owner_id=u)
    doc_id = r.document.id if hasattr(r, "document") and r.document else getattr(r, "document_id", None)
    print("ingested", doc_id, "A lexical size", a.lexical_size, "B lexical size", b.lexical_size)
    # worker B restarts later -> sees it; then A deletes
    await b.rebuild_lexical_index()
    n = await a.delete_document(doc_id, owner_id=u)
    print("deleted via A, chunks purged:", n, "A size", a.lexical_size, "B size", b.lexical_size)
    res_a = await a.retrieve("AURA-4419 runbook", owner_id=u)
    res_b = await b.retrieve("AURA-4419 runbook", owner_id=u)
    print("A results:", [x.chunk.content[:40] for x in res_a.chunks])
    print("B results:", [x.chunk.content[:40] for x in res_b.chunks])
asyncio.run(main())
