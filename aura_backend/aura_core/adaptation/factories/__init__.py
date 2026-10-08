"""Blueprint factories (AP6): tools, agents, workflows, UI manifest and evaluation suites.

Each factory turns part of a capability plan into declarative blueprint data. None of them
emits code: every output is a frozen domain value the unchanged core already knows how to check
and execute (ADR-018). What a factory cannot produce safely it reports as a reason, never a stub.
"""
