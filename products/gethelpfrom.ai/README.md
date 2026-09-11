# GetHelpFrom.ai

Product spec for the pain-point → ranked AI-solution funnel.

- Spec: [`SPEC.md`](SPEC.md)
- Canonical JSON: `questions.json`, `scenarios.json`, `scoring.json`
- Scorer: `score.py` (no LLM)
- Digest: `digest.schema.json`
- Persona provenance: `sources/`

```bash
python3 -m unittest discover -s products/gethelpfrom.ai/tests -v
```

Git files win. Do not copy questions into a CMS.
