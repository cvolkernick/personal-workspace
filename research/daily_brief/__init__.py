"""Daily Brief newspaper (#1091), served on the Horizon host at /daily-brief.

Not part of the Horizon model: it never reads world-state or model briefs.
Modules: store (edition JSON store + schema), redact (passes the stored
edition through; the paper is internal-only), render (pages + routes),
cli (publish CLI).
"""
