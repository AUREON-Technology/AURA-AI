"""AURA AI core -- the enterprise AI platform kernel.

A framework-free package: nothing here imports FastAPI, Celery, or SQLAlchemy. Web, worker, and
CLI entry points are adapters that construct an :class:`~aura_core.kernel.AIKernel` and call it.

Layering (enforced by ``tests/architecture/test_dependency_rules.py``)::

    domain/  ports/           stdlib only
    engines (prompts, context, memory, knowledge, tools, safety,
             planning, reasoning, execution, llm routing)
                              domain + ports only
    adapters/                 vendor SDKs, lazily imported
    api/                      FastAPI adapters

See ``docs/architecture/00-overview.md``.
"""

__version__ = "3.0.0"
__all__ = ["__version__"]
