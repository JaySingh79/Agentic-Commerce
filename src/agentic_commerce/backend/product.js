import { CATALOG_URL } from './search.js';
import { prompt } from './utils.js';


// Fetches product details from the catalog API using the provided token and product ID.
export async function getProductDetails(token, productId, selected = []) {
  const res = await fetch(CATALOG_URL, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`
    },
    body: JSON.stringify({
      jsonrpc: '2.0',
      method: 'tools/call',
      id: 2,
      params: {
        name: 'get_product',
        arguments: {
          meta: {
            'ucp-agent': {
              profile: 'https://shopify.dev/ucp/agent-profiles/2026-04-08/valid-with-capabilities.json'
            }
          },
          catalog: {
            id: productId,
            ...(selected.length ? { selected } : {})
          }
        }
      }
    })
  });

  const data = await res.json();
  return data.result?.structuredContent ?? null;
}

// 
export function displayProduct(product) {
  const featuredVariant = product.variants?.[0];
  const price = featuredVariant ? `$${(featuredVariant.price.amount / 100).toFixed(2)}` : '';
  const sellerName = featuredVariant?.seller?.name ?? '';
  const sellerDomain = featuredVariant?.seller?.domain ?? '';
  const variantTitle = featuredVariant?.title ?? '';
  
  console.log('\n── 3. Product Details ─────────────────────────────\n');
  console.log(`  Title:    ${product.title}${variantTitle ? ` - ${variantTitle}` : ''}`);
  console.log(`  Price:    ${price}`);
  console.log(`  Seller:   ${sellerName} (${sellerDomain})\n`);
  if (product.description?.html) {
    console.log(`  Summary:  ${product.description.html.replace(/<[^>]*>?/gm, '').slice(0, 160)}...\n`);
  }
}

export async function selectProduct(token, products) {
  if (!products || products.length === 0) return null;
  
  const pick = process.argv[3] || await prompt(`\x1b[1m  Select a product to view [1-${products.length}]:\x1b[0m  `);
  const index = Math.max(0, Math.min(products.length - 1, parseInt(pick || '1') - 1));
  const selectedProduct = products[index];

  console.log(`\n  Fetching details for: ${selectedProduct.title}...`);
  const details = await getProductDetails(token, selectedProduct.id);
  const product = details?.product;
  if (!product) {
    console.log('  Could not retrieve product details.');
    return null;
  }

  displayProduct(product);
  return product;
}
