# Saidcue landing page

Static page (`index.html`) plus a Cloudflare Pages Function (`functions/api/waitlist.js`) that stores waitlist signups in a D1 database.

## Deploy on Cloudflare (free)

1. **Domain**: buy saidcue.com at Cloudflare Registrar (Domain Registration), or add an existing domain (Websites > Add a domain, Free plan) and change its nameservers to the two Cloudflare gives you.
2. **Database**: Storage & Databases > D1 > Create database, name it `saidcue-waitlist`. Open it > Console, paste the contents of `schema.sql`, run it.
3. **Pages project**: Workers & Pages > Create > Pages > Connect to Git, pick this repo. Settings:
   - Production branch: `main`
   - Framework preset: None
   - Build command: leave empty
   - Root directory (under Advanced): `landing`
   - Build output directory: `/`

   Pages looks for the `functions/` folder in the root directory, so the root must be `landing`.
4. **Bind the database**: Pages project > Settings > Bindings > Add > D1 database. Variable name `DB`, pick `saidcue-waitlist`. Add it for Production (and Preview if you want). Redeploy.
5. **Custom domain**: Pages project > Custom domains > Set up a domain > `saidcue.com`, then add `www.saidcue.com`. Cloudflare creates the DNS records and the HTTPS certificate.
6. **Test**: open https://saidcue.com, submit a signup, then in D1 > Console run `SELECT * FROM waitlist;`.

## Environment variables

None are required. The form posts to `/api/waitlist` on the same domain.

Optional, to block bots: create a Turnstile widget (Turnstile > Add site), then add the secret key under Pages > Settings > Variables and Secrets as `TURNSTILE_SECRET` (type: Secret). The function enforces it when set. You also need to add the Turnstile widget and send its token as `turnstile` from the page.

## Reading signups

D1 > saidcue-waitlist > Console: `SELECT * FROM waitlist ORDER BY created_at DESC;` or export from the dashboard.
