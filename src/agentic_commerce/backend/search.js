import { prompt } from './utils.js';

// The official Shopify Global Catalog MCP endpoint
// Leave CATALOG_ID as '' to search across the entire Shopify Global Catalog,
// or set your custom catalog ID to scope the search to your saved catalog.
export const CATALOG_URL = 'https://catalog.shopify.com/api/ucp/mcp';
export const CATALOG_ID = '01m174jec82qbzbwv9rpbtjj6q';

export function showCatalog() {
  console.log('\n── 2. Search the Catalog ─────────────────────────\n');
  console.log(`  Catalog Endpoint: ${CATALOG_URL}`);
  console.log(`  Scope:            ${CATALOG_ID ? `Custom Catalog (${CATALOG_ID})` : 'Global Shopify Catalog'}\n`);
}

export function displayProducts(products) {
  console.log('\n── Results ────────────────────────────────────────\n');
  if (!products || products.length === 0) {
    console.log('  No products found matching your search.');
    return;
  }
  products.forEach((product, i) => {
    const price = product.price_range?.min?.amount 
      ? `$${(product.price_range.min.amount / 100).toFixed(2)}`
      : '—';
    const options = product.options?.map(o => `${o.name}: ${o.values.map(v => v.label).join(', ')}`).join('  |  ') ?? '—';
    console.log(`  [${i + 1}] ${product.title}  |  ${price}  |  ${options}`);
  });
  console.log();
}

export async function searchProducts(token, filters = {}) {
  const query = process.argv[2] || await prompt('\x1b[1m  Hello! What are you looking for today?\x1b[0m\n\n  > ');
  
  const catalog = {
    query: query.trim()
  };

  if (CATALOG_ID && CATALOG_ID.trim()) {
    catalog.catalog_id = CATALOG_ID.trim();
  }

  if (filters && Object.keys(filters).length > 0) {
    catalog.filters = filters;
  }

  const res = await fetch(CATALOG_URL, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`
    },
    body: JSON.stringify({
      jsonrpc: '2.0',
      method: 'tools/call',
      id: 1,
      params: {
        name: 'search_catalog',
        arguments: {
          meta: {
            'ucp-agent': {
              profile: 'https://shopify.dev/ucp/agent-profiles/2026-04-08/valid-with-capabilities.json'
            }
          },
          catalog
        }
      }
    })
  });

  let data;
  try {
    data = await res.json();
  } catch {
    const text = await res.text().catch(() => '');
    throw new Error(`Catalog API returned HTTP ${res.status} with non-JSON response: ${text}`);
  }

  if (data.error) {
    const details = data.error.data ? ` - ${JSON.stringify(data.error.data)}` : '';
    throw new Error(`Catalog API error (${data.error.code}): ${data.error.message}${details}`);
  }

  const structuredContent = data.result?.structuredContent;
  
  // If structured content has products, return it
  if (structuredContent?.products) {
    return structuredContent;
  }

  // Fallback: check if content array has parsed text JSON
  if (data.result?.content?.[0]?.text) {
    try {
      const parsed = JSON.parse(data.result.content[0].text);
      if (parsed.products) return parsed;
    } catch {}
  }

  return structuredContent ?? null;
}