# Decisions — Knowledge module (2026-11-22)

## D-001 — Licences allowed in the global corpus

- **Question.** Which licences may enter AURA's global (all-users) knowledge corpus?
- **Answer (USER-CONFIRMED, 2026-11-22):** "CC0, CC BY, permissive". Share-alike (CC BY-SA) and
  non-commercial (NC) licences are **not** allowed.
- **Owner:** the user. **Source:** this conversation.
- **Affects:** KM-04 (the `license_status` check at registration), KM-03 (which adapters are worth building
  first), the citation format (attribution is still required for CC BY), and the report's source list.
- **Not decided by this answer:** whether a share-alike source may be used as a *private* or per-workspace
  source rather than global; who reviews a licence; the exact permissive list (MIT, BSD, Apache-2.0,
  PostgreSQL licence, PSF are the obvious members). These are open and can be deferred to PLAN.

### Effect on the strategy report's sources

| Source | Under D-001 | Note |
|---|---|---|
| Wikidata | **Allowed** | CC0 per the report; re-verify before registering |
| OpenAlex metadata | **Allowed** | CC0 per the report |
| arXiv metadata | **Allowed**; full text only per paper | metadata is not the paper; record the paper's licence |
| PMC open-access | **Allowed only for CC0 / CC BY articles** | filter per article; PubMed bibliographic records need their own check |
| World Bank datasets | **Allowed** where CC BY 4.0 | the report says "generally", so per dataset |
| Common Voice | Allowed (CC0) | but it is audio: speech evaluation, not the registry |
| Official software docs (Python, FastAPI, PostgreSQL, Celery, PyTorch, vLLM, Ollama, Flutter, Dart, Docker, MCP, Qdrant, Redis) | **Check each** | most are permissive, but docs can carry a different licence from the code; Redis and Qdrant docs in particular need a look |
| **Wikipedia (EN and AR)** | **Excluded** | CC BY-SA; this removes the report's anchor for general and Arabic encyclopaedic knowledge |
| **Stack Exchange** | **Excluded** | CC BY-SA, plus terms I could not read |
| **OpenStax** | **Excluded** | the cited page says CC BY-NC-SA |
| GitHub repositories | Per-repo only | MIT, BSD, Apache-2.0 repos; no licence means no |
| Hugging Face datasets, Gutenberg, DOAB | Per item | dataset card or book licence decides |
| Egyptian legislation, CAPMAS, MoF | **Unknown** | reuse terms unverified; default to excluded until read |
| Common Crawl | Excluded as a source | candidate discovery only |

Consequence to plan for: without Wikipedia, general knowledge leans on Wikidata (facts, not prose),
OpenAlex/arXiv/PMC (research) and official documentation. Arabic prose coverage needs another source, still
to be found.
