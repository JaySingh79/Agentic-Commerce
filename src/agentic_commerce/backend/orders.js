import crypto from 'node:crypto';
import { getMcpEndpoint } from './mcp.js';

export const AGENT_PROFILE = 'https://shopify.dev/ucp/agent-profiles/2026-04-08/valid-with-capabilities.json';

export async function getOrderAccessToken() {
  const clientId = (process.env.CLIENT_ID || process.env.SHOPIFY_CLIENT_ID)?.trim();
  const clientSecret = (process.env.CLIENT_SECRET || process.env.SHOPIFY_CLIENT_SECRET)?.trim();

  const res = await fetch('https://api.shopify.com/auth/access_token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      client_id: clientId,
      client_secret: clientSecret,
      grant_type: 'client_credentials'
    })
  });

  const data = await res.json();
  if (!data.access_token) {
    throw new Error(`Order token mint failed: ${JSON.stringify(data)}`);
  }

  return data.access_token;
}

export async function getOrder(token, orderId, merchantUrl) {
  const origin = new URL(merchantUrl).origin;
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
      id: 9,
      params: {
        name: 'get_order',
        arguments: {
          id: orderId,
          meta: { 'ucp-agent': { profile: AGENT_PROFILE } }
        }
      }
    })
  });

  const data = await res.json();
  if (data?.result?.isError) {
    const message = data.result.structuredContent?.messages?.[0];
    throw new Error(`get_order error: ${message?.code} (${message?.severity})`);
  }

  const order = data.result?.structuredContent?.order || data.result?.structuredContent;
  if (!order) {
    throw new Error(`get_order failed: ${JSON.stringify(data)}`);
  }

  return order;
}

export function displayOrder(order) {
  const total = order.totals?.find(t => t.type === 'total')?.amount ?? 0;
  console.log('\n── Order Summary ──────────────────────────────────\n');
  console.log(`  Order:       ${order.label || order.id}`);
  console.log(`  Total:       $${(total / 100).toFixed(2)} ${order.currency || 'USD'}`);
  if (order.permalink_url) {
    console.log(`  Status Page: ${order.permalink_url}\n`);
  }

  if (order.line_items?.length) {
    console.log('  Items:');
    for (const line of order.line_items) {
      if (line.quantity?.total === 0) continue;
      const count = line.quantity?.total ?? line.quantity ?? 1;
      const title = line.item?.title || line.title || 'Item';
      const status = line.status || 'confirmed';
      console.log(`    · ${title} (x${count}, ${status})`);
    }
  }

  if (order.fulfillment?.events?.length) {
    console.log('\n  Fulfillment Timeline:');
    for (const event of order.fulfillment.events) {
      const when = new Date(event.occurred_at).toLocaleString();
      console.log(`    · ${event.type.padEnd(12)} ${when}`);
    }
  }

  if (order.adjustments?.length) {
    console.log('\n  Adjustments:');
    for (const adj of order.adjustments) {
      const amount = adj.totals?.[0]?.amount ?? 0;
      console.log(`    · ${adj.type.padEnd(14)} $${(amount / 100).toFixed(2)}`);
    }
  }
}

export function verifyOrderWebhook(rawBody, headers, sharedSecret) {
  const provided = headers['x-shopify-hmac-sha256'];
  if (!provided) return false;
  const computed = crypto
    .createHmac('sha256', sharedSecret)
    .update(rawBody)
    .digest('base64');
  const providedBuf = Buffer.from(provided, 'utf8');
  const computedBuf = Buffer.from(computed, 'utf8');
  if (providedBuf.length !== computedBuf.length) return false;
  return crypto.timingSafeEqual(providedBuf, computedBuf);
}
