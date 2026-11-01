// Usage: node gen-image.mjs "<prompt>" <aspect_ratio> <n> <outprefix>
// Reads MINIMAX_API_KEY from tools/.env. Saves PNG/JPEG files into tools/generated/.
import fs from "node:fs";
const env = Object.fromEntries(
  fs.readFileSync(new URL("./.env", import.meta.url), "utf8").split(/\r?\n/)
    .filter(l => l.includes("=") && !l.trim().startsWith("#"))
    .map(l => [l.slice(0, l.indexOf("=")).trim(), l.slice(l.indexOf("=") + 1).trim().replace(/^["']|["']$/g, "")])
);
const [prompt, aspect = "16:9", n = "1", prefix = "img"] = process.argv.slice(2);
if (!env.MINIMAX_API_KEY) { console.error("MINIMAX_API_KEY missing in tools/.env"); process.exit(1); }
const res = await fetch("https://api.minimax.io/v1/image_generation", {
  method: "POST",
  headers: { Authorization: `Bearer ${env.MINIMAX_API_KEY}`, "Content-Type": "application/json" },
  body: JSON.stringify({ model: "image-01", prompt, aspect_ratio: aspect, response_format: "base64", n: Number(n) }),
});
const out = await res.json();
if (out.base_resp?.status_code !== 0) { console.error("API error:", out.base_resp?.status_code, out.base_resp?.status_msg); process.exit(1); }
out.data.image_base64.forEach((b64, i) => {
  const f = new URL(`./generated/${prefix}-${i + 1}.jpg`, import.meta.url);
  fs.writeFileSync(f, Buffer.from(b64, "base64"));
  console.log("saved", `generated/${prefix}-${i + 1}.jpg`);
});
console.log("success:", out.metadata?.success_count, "failed:", out.metadata?.failed_count);
