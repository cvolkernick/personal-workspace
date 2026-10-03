#!/usr/bin/env python3
"""Live entry point. The unit runs this file from ~/bin next to bland_relay.py."""
import sys

try:
    from bland_relay import main
except ImportError:
    sys.stderr.write("bland_relay.py must sit next to bland-relay.py\n")
    raise

if __name__ == "__main__":
    main()
