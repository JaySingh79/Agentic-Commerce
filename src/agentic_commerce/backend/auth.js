export async function getAccessToken() {
  const clientId = process.env.CLIENT_ID;
  const clientSecret = process.env.CLIENT_SECRET;
  const res = await fetch('https://api.shopify.com/auth/access_token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      client_id: clientId,
      client_secret: clientSecret,
      grant_type: 'client_credentials'
    })
  });
  const { access_token } = await res.json();
  const [, payload] = access_token.split('.');
  const { scopes, exp, limits } = JSON.parse(Buffer.from(payload, 'base64').toString('utf8'));
  console.log('\n── 1. Authentication ─────────────────────────\n');
  console.log(`  Scopes:  ${scopes}`);
  console.log(`  Expires: ${new Date(exp * 1000).toLocaleTimeString()}`);
  return access_token;
}