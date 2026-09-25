# GetHelpFrom.ai

Product spec for the pain-point → ranked AI-solution funnel.

- Spec: [`SPEC.md`](SPEC.md)
- Canonical JSON: `questions.json`, `scenarios.json`, `scoring.json`
- Scorer: `score.py` (no LLM)
- Digest: `digest.schema.json`
- Persona provenance: `sources/`

```bash
python3 -m unittest discover -s products/gethelpfrom.ai/tests -v
npm --prefix products/gethelpfrom.ai test
npm --prefix products/gethelpfrom.ai run dev
```

The funnel shell is the Next.js app in this directory. It reads `questions.json`, `scenarios.json`, and `scoring.json` from here. Do not copy them into the UI or a CMS.

Preview env: copy `.env.example`. Ops mail uses `GETHELPFROM_OPS_EMAIL` and Resend. With those unset, a lead is still stored under `GETHELPFROM_DATA_DIR` (default OS temp) and nothing is auto-accepted. Vercel project root: `products/gethelpfrom.ai`. Domain registration and the public gate stay on #646.

Git files win. Do not copy questions into a CMS.
