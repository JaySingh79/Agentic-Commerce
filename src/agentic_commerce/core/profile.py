"""Phase 1: UCP agent profile and capability / trust-tier negotiation.

Builds the ``ucp-agent`` capability profile advertised on every MCP call and
resolves the effective trust tier from the credentials available (see
project_memory.md section 2).
"""

from agentic_commerce.core.models import TrustTier, UCPProfile
from agentic_commerce.ucp.client import AGENT_PROFILE

# Capability namespaces the platform agent declares support for.
DEFAULT_CAPABILITIES = [
    "catalog.search",
    "catalog.get_product",
    "cart.create",
    "cart.update",
    "checkout.create",
    "checkout.complete",
    "order.get",
    "ap2.mandate",
]


def resolve_trust_tier(
    has_token: bool = False,
    has_signature: bool = False,
    buyer_linked: bool = False,
) -> TrustTier:
    """Resolves the highest trust tier available for the given credentials.

    Precedence (highest first): buyer-linked > token (client_credentials) >
    signed (RFC 9421) > anonymous.
    """
    if buyer_linked:
        return TrustTier.BUYER_LINKED
    if has_token:
        return TrustTier.TOKEN
    if has_signature:
        return TrustTier.SIGNED
    return TrustTier.ANONYMOUS


def build_agent_profile(
    capabilities: list[str] | None = None,
    trust_tier: TrustTier = TrustTier.TOKEN,
) -> UCPProfile:
    """Builds the platform agent's UCP capability profile."""
    return UCPProfile(
        profile_url=AGENT_PROFILE,
        capabilities=capabilities or list(DEFAULT_CAPABILITIES),
        trust_tier=trust_tier,
    )


def negotiate_capabilities(
    agent_profile: UCPProfile, merchant_capabilities: list[str]
) -> list[str]:
    """Returns the intersection of agent and merchant capabilities (server-selects)."""
    merchant_set = set(merchant_capabilities)
    return [cap for cap in agent_profile.capabilities if cap in merchant_set]
