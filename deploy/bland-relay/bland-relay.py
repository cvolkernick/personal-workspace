#!/usr/bin/env python3
"""Live entry point. The unit runs this file from ~/bin.

bland_relay.py and the relay_*.py modules it loads must sit next to this file,
along with caller_context.py and turo_email.py. Modes and rollback are in the
README Deploy section.
"""
import sys

try:
    from bland_relay import main
except ImportError as exc:
    sys.stderr.write("bland_relay.py and the relay_*.py modules must sit next to bland-relay.py\n")
    detail = str(exc).strip()
    if detail:
        sys.stderr.write(detail + "\n")
    raise

if __name__ == "__main__":
    main()
