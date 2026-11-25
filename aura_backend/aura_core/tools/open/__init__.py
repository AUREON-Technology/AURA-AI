"""Code-analysis and verification helpers exposed to the agent through ``tools/analysis.py``,
``tools/open_exec.py`` and ``tools/desktop_ops.py``.

The classes here are plain, synchronous and JSON-in/dict-out. They are *not* tools themselves:
``FunctionTool`` requires an async handler, so each group has a builder that wraps them. Most
classes are deliberately not wrapped -- superseded by an engine the platform already has,
deferred to an ``aura_hostd`` RPC, or not exposed -- and each of those says so at the top of its
file. ``_classification.py`` is the one list of which module is which (ADR-036); the architecture
tests fail on a module that is not in it.
"""
