"""Compatibility shim. The UCP client now lives in ``agentic_commerce.ucp.client``.

Kept so existing imports (``from agentic_commerce.backend.ucp_client import
ShopifyUcpClient``) continue to resolve after the Phase 2 reorganization.
"""

from agentic_commerce.ucp.client import (
    AGENT_PROFILE,
    CATALOG_URL,
    ShopifyUcpClient,
)

__all__ = ["ShopifyUcpClient", "CATALOG_URL", "AGENT_PROFILE"]
