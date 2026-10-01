"""Connector adapters, one module per source technology (ADR-021).

Each module imports its vendor driver lazily, inside functions, so importing this package never
needs the driver installed and never opens a connection.
"""
