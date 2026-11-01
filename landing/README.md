# Saidcue landing page

Static page (`public/index.html`) plus a Cloudflare Worker (`src/worker.js`) that stores waitlist signups in a D1 database. Config is in `wrangler.jsonc`.

## Deploy on Cloudflare (free)

1. **Domain**: saidcue.com bought at Cloudflare Registrar (DNS zone is created automatically).
2. **Database**: Storage & Databases > D1 > Create database, name it `saidcue-waitlist`. Open it > Console, paste `schema.sql`, run it. Copy the **Database ID** from the database's page.
3. **Config**: in `wrangler.jsonc`, replace `PASTE_YOUR_D1_DATABASE_ID_HERE` with that ID, commit and push.
4. **Worker project**: Workers & Pages > Create > Import a repository > pick this repo. Settings:
   - Root directory: `landing`
   - Build command: leave empty
   - Deploy command: `npx wrangler deploy` (default)
   - Non-production branch deploy command: `npx wrangler versions upload` (default)
5. **Domain**: `wrangler.jsonc` lists `saidcue.com` and `www.saidcue.com` as custom domains, so the first deploy attaches them and creates the DNS records and certificate. If a domain already has a DNS record, delete that record first.
6. **Test**: open https://saidcue.com, submit a signup, then in D1 > Console run `SELECT * FROM waitlist;`.

## Environment variables

None required. The form posts to `/api/waitlist` on the same domain, and the database arrives through the `DB` binding in `wrangler.jsonc`.

Optional, to block bots: create a Turnstile widget (Turnstile > Add site), then add `TURNSTILE_SECRET` under the Worker > Settings > Variables and Secrets (type: Secret). The Worker enforces it when set. You also need to add the widget to the page and send its token as `turnstile`.

## Reading signups

D1 > saidcue-waitlist > Console: `SELECT * FROM waitlist ORDER BY created_at DESC;`
