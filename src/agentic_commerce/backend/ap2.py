"""Compatibility shim. AP2 mandate engine now lives in ``agentic_commerce.ap2.mandate``.

Kept so existing imports (``from agentic_commerce.backend.ap2 import AP2Engine``)
and tests continue to resolve after the Phase 4 reorganization.
"""

from agentic_commerce.ap2.mandate import STATUS_PENDING, AP2Engine

__all__ = ["AP2Engine", "STATUS_PENDING"]
