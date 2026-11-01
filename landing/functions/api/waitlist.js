// Cloudflare Pages Function: POST /api/waitlist
// Needs a D1 binding named DB (see landing/README.md).
const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const clip = (v, n) => (typeof v === "string" ? v.trim().slice(0, n) : "");

const json = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

export async function onRequestPost({ request, env }) {
  let data;
  try { data = await request.json(); } catch { return json({ error: "bad request" }, 400); }

  // honeypot: real users never fill this hidden field
  if (clip(data.website, 100)) return json({ ok: true });

  const email = clip(data.email, 254).toLowerCase();
  if (!EMAIL_RE.test(email)) return json({ error: "invalid email" }, 400);

  // optional bot check: only enforced when TURNSTILE_SECRET is set as a secret
  if (env.TURNSTILE_SECRET) {
    const form = new FormData();
    form.append("secret", env.TURNSTILE_SECRET);
    form.append("response", clip(data.turnstile, 2048));
    const r = await fetch("https://challenges.cloudflare.com/turnstile/v0/siteverify", { method: "POST", body: form });
    const out = await r.json();
    if (!out.success) return json({ error: "bot check failed" }, 400);
  }

  try {
    await env.DB.prepare(
      "INSERT INTO waitlist (email, role, pro_feature, source) VALUES (?1, ?2, ?3, ?4) " +
      "ON CONFLICT(email) DO UPDATE SET role = excluded.role, pro_feature = excluded.pro_feature"
    ).bind(email, clip(data.role, 80), clip(data.pro_feature, 120), clip(data.source, 60)).run();
  } catch (e) {
    return json({ error: "server error" }, 500);
  }
  return json({ ok: true });
}

export const onRequest = () => json({ error: "method not allowed" }, 405);
