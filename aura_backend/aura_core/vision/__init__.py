"""Image understanding (ADR-031): describe attached images as DATA-channel evidence."""

from .engine import PERCEPTION_CONTEXT_ID, Perception, VisionEngine

__all__ = ["VisionEngine", "Perception", "PERCEPTION_CONTEXT_ID"]
