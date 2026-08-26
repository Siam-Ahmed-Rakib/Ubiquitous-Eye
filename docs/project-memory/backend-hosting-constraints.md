---
name: backend-hosting-constraints
description: "Which backend hosts are actually viable for this project, and why the obvious free ones are not"
metadata: 
  node_type: memory
  type: project
  originSessionId: 241d6d28-6325-4f26-9185-9cf014e2bdd1
  modified: 2026-07-28T21:25:46.707Z
---

Verified 2026-07-29 while trying to deploy the backend. The free-tier landscape had changed since my training data, so **re-verify before recommending any host** — I recommended Hugging Face Spaces and it turned out to be impossible.

**Ruled out, with reasons:**
- **Hugging Face Spaces** — creating a Docker *or* Gradio Space now returns HTTP 402 and requires PRO (~$20/mo). Only static HTML Spaces and 2 ZeroGPU Gradio Spaces are free. Confirmed empirically: `create_repo(space_sdk="docker", private=False)` → 402, `space_sdk="static"` → OK. The `deploy/huggingface/` scaffold in the repo is therefore dead weight.
- **Koyeb** — acquired by Mistral AI in early 2026; free Starter tier closed to new users.
- **Render** — free tier scales to zero after 15 min (~1 min cold start), 512 MB / **0.1 CPU**, and now wants a card for Docker specifically. 0.1 CPU is unusable for a whole-month Sentinel composite plus 4 gradient-boosting models.
- **Back4App** — 250 MB storage; the image alone is ~2.4 GB.
- Anything serverless (Vercel/Netlify/Lambda) — 250 MB unzipped cap, and request timeouts far below the minutes a composite takes.

**What the user has:** Azure for Students ($100/yr, renewable yearly while a full-time student, **no credit card**). No credit/debit card otherwise, which is what closes off Oracle Cloud Always Free, Fly.io, and Google Cloud Run.

**Credit exhaustion is safe:** with no card attached, Azure disables the subscription and decommissions services. It cannot bill them.

**Sizing:** models are only 5.6 MB — moving inference elsewhere would buy nothing. The weight is the Python wheels (xgboost + lightgbm + catboost + sklearn + scipy + pandas). After `--preload`, idle RSS is **130 MB** (was ~500 MB), so a small Container Apps SKU fits. Estimated $5-10/mo at min-replicas=1 → 10-20 months of the $100. Prefer the **Southeast Asia (Singapore)** region: co-located with the Supabase instance and ~60 ms from Bangladesh users vs ~250 ms from the US.

Not yet decided — the user paused this to do database work. See [[result-cache-architecture]].
