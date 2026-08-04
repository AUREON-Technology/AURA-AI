"""The AI kernel -- the pipeline orchestrator.

Owns sequence and policy; every capability is behind a port or an engine. See
``docs/architecture/00-overview.md`` §2.
"""

from .kernel import AIKernel, KernelDependencies, TurnRequest, TurnResult

__all__ = ["AIKernel", "KernelDependencies", "TurnRequest", "TurnResult"]
