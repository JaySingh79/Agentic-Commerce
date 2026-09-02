/**
 * Utility for discovering merchant UCP capabilities and MCP endpoints.
 */

export async function getMcpEndpoint(merchantOrigin) {
  const origin = merchantOrigin.startsWith('http') ? merchantOrigin : `https://${merchantOrigin}`;

  try {
    const res = await fetch(`${origin}/.well-known/ucp`, {
      headers: { 'Accept': 'application/json' }
    });

    if (res.ok) {
      const profile = await res.json();
      if (profile.services?.mcp?.endpoint) {
        return profile.services.mcp.endpoint;
      }
    }
  } catch {
    // Fallback to standard Shopify UCP route
  }

  return `${origin}/api/ucp/mcp`;
}
