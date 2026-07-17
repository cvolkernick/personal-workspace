"""Top-level orchestra dashboard — cross-domain coordination for personal-workspace.

Aggregates strategy, workflow, finance/treasury, fitness/health, and time-allocation
into one payload with synergies and a coordinated action plan.
"""

try:
    from .payload import build_orchestra_payload
except ImportError:
    from payload import build_orchestra_payload

__all__ = ["build_orchestra_payload"]
