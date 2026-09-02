/**
 * Discovers a merchant's UCP profile and calls Cart MCP.
 */

export async function getMerchantUcpProfile(merchantDomain) {
  const origin = merchantDomain.startsWith('http') ? merchantDomain : `https://${merchantDomain}`;
  console.log(`\n── 4. Discover Merchant UCP Profile ───────────────\n`);
  console.log(`  Querying: ${origin}/.well-known/ucp`);

  try {
    const res = await fetch(`${origin}/.well-known/ucp`, {
      headers: { 'Accept': 'application/json' }
    });

    if (res.ok) {
      const profile = await res.json();
      console.log(`  ✓ UCP Version:     ${profile.ucp?.version || '2026-04-08'}`);
      console.log(`  ✓ MCP Endpoint:    ${profile.services?.mcp?.endpoint || `${origin}/api/ucp/mcp`}`);
      return profile;
    }
  } catch (err) {
    console.log(`  (Note: Fallback to standard endpoint ${origin}/api/ucp/mcp)`);
  }

  return {
    services: {
      mcp: { endpoint: `${origin}/api/ucp/mcp` }
    }
  };
}

export async function createMerchantCart(merchantDomain, variantId, quantity = 1) {
  const origin = merchantDomain.startsWith('http') ? merchantDomain : `https://${merchantDomain}`;
  const mcpEndpoint = `${origin}/api/ucp/mcp`;

  console.log(`\n── 5. Create Cart via Merchant Cart MCP ──────────\n`);
  console.log(`  Merchant Endpoint: ${mcpEndpoint}`);
  console.log(`  Variant ID:        ${variantId}`);
  console.log(`  Quantity:          ${quantity}`);

  const res = await fetch(mcpEndpoint, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      jsonrpc: '2.0',
      method: 'tools/call',
      id: 3,
      params: {
        name: 'create_cart',
        arguments: {
          meta: {
            'ucp-agent': {
              profile: 'https://shopify.dev/ucp/agent-profiles/2026-04-08/valid-with-capabilities.json'
            }
          },
          cart: {
            line_items: [
              {
                quantity,
                item: {
                  id: variantId
                }
              }
            ],
            context: {
              address_country: 'US'
            }
          }
        }
      }
    })
  });

  let data;
  try {
    data = await res.json();
  } catch {
    const text = await res.text().catch(() => '');
    throw new Error(`Merchant MCP returned non-JSON (HTTP ${res.status}): ${text}`);
  }

  if (data.error) {
    const details = data.error.data ? ` - ${JSON.stringify(data.error.data)}` : '';
    throw new Error(`Cart MCP Error (${data.error.code}): ${data.error.message}${details}`);
  }

  const cart = data.result?.structuredContent?.cart || data.result?.structuredContent;
  if (cart && cart.id) {
    console.log('\n── Cart Created Successfully ──────────────────────\n');
    console.log(`  Cart ID:      ${cart.id}`);
    console.log(`  Currency:     ${cart.currency || 'USD'}`);
    if (cart.totals) {
      cart.totals.forEach(t => console.log(`  ${t.display_text || t.type}:  $${(t.amount / 100).toFixed(2)}`));
    }
    if (cart.continue_url) {
      console.log(`\n  Checkout / Continue URL:\n  🔗 ${cart.continue_url}\n`);
    }
  } else {
    console.log('\n── Raw Merchant MCP Response ──────────────────────\n');
    console.log(JSON.stringify(data, null, 2));
  }

  return cart;
}
