"""Top-level orchestra dashboard — cross-domain coordination for personal-workspace.

Aggregates strategy, workflow, finance/treasury, fitness/health, time-allocation,
and IoT into one payload with synergies, priorities, attention, and freshness.
"""

try:
    from .payload import build_orchestra_payload
except ImportError:
    from payload import build_orchestra_payload

__all__ = ["build_orchestra_payload"]
