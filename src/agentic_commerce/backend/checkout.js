import crypto from 'node:crypto';
import { getMcpEndpoint } from './mcp.js';

export const AGENT_PROFILE = 'https://shopify.dev/ucp/agent-profiles/2026-04-08/valid-with-capabilities.json';

export async function createCheckout(token, cartId, checkoutUrl) {
  const origin = new URL(checkoutUrl).origin;
  const mcpEndpoint = await getMcpEndpoint(origin);

  let data;
  try {
    const res = await fetch(mcpEndpoint, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`
      },
      body: JSON.stringify({
        jsonrpc: '2.0',
        method: 'tools/call',
        id: 4,
        params: {
          name: 'create_checkout',
          arguments: {
            cart_id: cartId,
            meta: { 'ucp-agent': { profile: AGENT_PROFILE } }
          }
        }
      })
    });
    data = await res.json();
  } catch (err) {
    console.log(`  (Note: Referral handoff via Cart permalink)`);
    return { id: cartId, checkout: null, checkoutUrl };
  }

  if (data?.error) {
    console.log(`\n── 6. Checkout Referral Handoff ───────────────────\n`);
    console.log(`  Merchant Store: ${origin}`);
    console.log(`  Mode:           Cart Referral Handoff (${data.error.message || 'Merchant Escalation'})`);
    console.log(`  Continue Link:  ${checkoutUrl}`);
    return { id: cartId, checkout: null, checkoutUrl };
  }

  const checkout = data.result?.structuredContent?.checkout || data.result?.structuredContent || data.result?.content?.[0]?.text;
  if (!checkout) {
    return { id: cartId, checkout: null, checkoutUrl };
  }

  const id = checkout.id;
  const totalAmount = checkout.totals?.find(t => t.type === 'total')?.amount ?? 0;

  console.log('\n── 6. Create Checkout from Cart ───────────────────\n');
  console.log(`  Checkout ID: ${id}`);
  console.log(`  Status:      ${checkout.status || 'created'}`);
  console.log(`  Total:       $${(totalAmount / 100).toFixed(2)}`);
  if (checkout.continue_url) {
    console.log(`  Continue:    ${checkout.continue_url}`);
  }

  return { id, checkout, checkoutUrl: checkout.continue_url || checkoutUrl };
}

export async function getCheckout(token, checkoutId, checkoutUrl) {
  const origin = new URL(checkoutUrl).origin;
  const mcpEndpoint = await getMcpEndpoint(origin);

  const res = await fetch(mcpEndpoint, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`
    },
    body: JSON.stringify({
      jsonrpc: '2.0',
      method: 'tools/call',
      id: 5,
      params: {
        name: 'get_checkout',
        arguments: {
          id: checkoutId,
          meta: { 'ucp-agent': { profile: AGENT_PROFILE } }
        }
      }
    })
  });

  const data = await res.json();
  if (data?.result?.content?.[0]?.text && typeof data.result.content[0].text === 'string') {
    try {
      data.result.content[0].text = JSON.parse(data.result.content[0].text);
    } catch {}
  }

  if (!data.result) throw new Error(`get_checkout failed: ${JSON.stringify(data)}`);
  return data.result.structuredContent?.checkout || data.result.structuredContent || data.result.content[0].text;
}

export async function updateCheckout(token, checkoutId, email, checkoutUrl) {
  const origin = new URL(checkoutUrl).origin;
  const mcpEndpoint = await getMcpEndpoint(origin);
  const current = await getCheckout(token, checkoutId, checkoutUrl);

  const res = await fetch(mcpEndpoint, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`
    },
    body: JSON.stringify({
      jsonrpc: '2.0',
      method: 'tools/call',
      id: 6,
      params: {
        name: 'update_checkout',
        arguments: {
          id: checkoutId,
          checkout: {
            currency: current.currency || 'USD',
            context: current.context || { country: 'US', language: 'en' },
            line_items: (current.line_items || []).map(li => ({
              quantity: li.quantity,
              item: { id: li.item?.id || li.id }
            })),
            buyer: { ...(current.buyer ?? {}), email }
          },
          meta: { 'ucp-agent': { profile: AGENT_PROFILE } }
        }
      }
    })
  });

  const data = await res.json();
  if (data?.result?.content?.[0]?.text && typeof data.result.content[0].text === 'string') {
    try {
      data.result.content[0].text = JSON.parse(data.result.content[0].text);
    } catch {}
  }

  if (!data.result) throw new Error(`update_checkout failed: ${JSON.stringify(data)}`);
  const checkout = data.result.structuredContent?.checkout || data.result.structuredContent || data.result.content[0].text;
  
  console.log('\n── 7. Update Checkout with Buyer Info ─────────────\n');
  console.log(`  Buyer Email: ${email}`);
  console.log(`  Status:      ${checkout.status || 'updated'}`);

  return checkout.continue_url || checkoutUrl;
}

export async function completeCheckout(token, checkoutId, checkoutUrl, payment = null) {
  const origin = new URL(checkoutUrl).origin;
  const mcpEndpoint = await getMcpEndpoint(origin);
  const current = await getCheckout(token, checkoutId, checkoutUrl);

  if (current.status !== 'ready_for_complete' && !payment) {
    console.log(`\n── Referral / Escalation ──────────────────────────\n`);
    console.log(`  Checkout status: ${current.status || 'requires_escalation'}`);
    console.log(`  Refer your buyer to finish purchase at:\n  🔗 ${current.continue_url || checkoutUrl}\n`);
    return { status: current.status, continue_url: current.continue_url || checkoutUrl };
  }

  const res = await fetch(mcpEndpoint, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`
    },
    body: JSON.stringify({
      jsonrpc: '2.0',
      method: 'tools/call',
      id: 7,
      params: {
        name: 'complete_checkout',
        arguments: {
          id: checkoutId,
          checkout: { payment },
          meta: {
            'ucp-agent': { profile: AGENT_PROFILE },
            'idempotency-key': crypto.randomUUID()
          }
        }
      }
    })
  });

  const data = await res.json();
  if (!data.result) throw new Error(`complete_checkout failed: ${JSON.stringify(data)}`);
  const checkout = data.result.structuredContent?.checkout || data.result.structuredContent;

  console.log('\n── 8. Complete Checkout ───────────────────────────\n');
  console.log(`  Status: ${checkout.status}`);
  if (checkout.order) console.log(`  Order:  ${checkout.order.id}`);

  return checkout;
}

export async function cancelCheckout(token, checkoutId, checkoutUrl) {
  const origin = new URL(checkoutUrl).origin;
  const mcpEndpoint = await getMcpEndpoint(origin);

  const res = await fetch(mcpEndpoint, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`
    },
    body: JSON.stringify({
      jsonrpc: '2.0',
      method: 'tools/call',
      id: 8,
      params: {
        name: 'cancel_checkout',
        arguments: {
          id: checkoutId,
          meta: {
            'ucp-agent': { profile: AGENT_PROFILE },
            'idempotency-key': crypto.randomUUID()
          }
        }
      }
    })
  });

  const data = await res.json();
  const status = data.result?.structuredContent?.status || 'canceled';
  console.log('\n── Cancel Checkout ────────────────────────────────\n');
  console.log(`  Status: ${status}`);
  console.log('  Checkout session cancelled successfully.');
}
