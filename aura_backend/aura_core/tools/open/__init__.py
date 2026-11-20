"""Code-analysis and verification helpers exposed to the agent through ``tools/analysis.py``,
``tools/open_exec.py`` and ``tools/desktop_ops.py``.

The classes here are plain, synchronous and JSON-in/dict-out. They are *not* tools themselves:
``FunctionTool`` requires an async handler, so each group has a builder that wraps them. Six
classes are deliberately not wrapped because the platform already has an engine for the job --
see the ``Superseded`` note at the top of each of those modules.
"""
