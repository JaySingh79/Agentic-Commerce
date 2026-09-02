import { prompt } from './utils.js';
import { getAccessToken } from './auth.js';
import { searchProducts, displayProducts, showCatalog } from './search.js';
import { selectProduct } from './product.js';
import { getMerchantUcpProfile, createMerchantCart } from './cart.js';
import { createCheckout, updateCheckout, cancelCheckout } from './checkout.js';

async function main() {
  // 1. Authentication
  const token = await getAccessToken();

  // 2. Search the Global Catalog
  showCatalog();
  const searchResult = await searchProducts(token);   
  const products = searchResult?.products || [];
  displayProducts(products);

  if (!products.length) return;

  // 3. Select a Product & Fetch Merchant/Variant Details
  const product = await selectProduct(token, products);
  if (!product || !product.variants?.length) return;

  const variant = product.variants[0];
  const merchantDomain = variant.seller?.domain || variant.seller?.url;

  if (!merchantDomain) {
    console.log('No merchant domain found for this variant.');
    return;
  }

  // 4. Discover Merchant UCP Profile
  await getMerchantUcpProfile(merchantDomain);

  // 5. Build a Cart via Merchant Cart MCP
  const cart = await createMerchantCart(merchantDomain, variant.id, 1);
  if (!cart?.id) return;

  const checkoutUrl = cart.continue_url || variant.seller?.url || `https://${merchantDomain}`;

  // 6. Convert Cart to Checkout Session via Checkout MCP
  const { id: checkoutId, checkout, checkoutUrl: finalCheckoutUrl } = await createCheckout(token, cart.id, checkoutUrl);

  let finalUrl = finalCheckoutUrl || checkoutUrl;

  if (checkout) {
    // 7. Update Checkout with Buyer Information
    const email = process.argv[4] || await prompt('\n\x1b[1m  Enter your buyer email address:\x1b[0m  ');
    finalUrl = await updateCheckout(token, checkoutId, email.trim() || 'buyer@example.com', checkoutUrl);
  }

  // 8. Add UTM Attribution for the Agent
  const attributedUrl = new URL(finalUrl);
  attributedUrl.searchParams.set('utm_source', 'agentic_commerce');
  attributedUrl.searchParams.set('utm_medium', 'ai_agent');

  console.log('\n── 8. Buyer Referral Handoff ──────────────────────\n');
  console.log('  Refer your buyer to complete the purchase at:');
  console.log(`  🔗 ${attributedUrl.toString()}\n`);

  // 9. Clean up demo session if checkout was created
  if (checkout) {
    const cancel = process.argv[5] || await prompt('\x1b[1m  Press [Enter] or type "c" to clean up and cancel the checkout session:\x1b[0m  ');
    if (cancel !== 'no') {
      await cancelCheckout(token, checkoutId, checkoutUrl);
    }
  }
}

main().catch(err => console.error('Request failed:', err));