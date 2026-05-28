#!/usr/bin/env python3
"""Wiz lights control script"""
import asyncio
import json
import sys
from pywizlight import wizlight, PilotBuilder

BULBS_FILE = "/home/forge-agent/.openclaw/workspace/skills/wiz-lights/bulbs.json"

# Color presets
COLORS = {
    "white": (255, 255, 255),
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
    "cyan": (0, 255, 255),
    "magenta": (255, 0, 255),
    "yellow": (255, 255, 0),
    "off": None,
}

def load_bulbs():
    with open(BULBS_FILE) as f:
        return json.load(f)

async def set_light(name, color, brightness=200):
    bulbs = load_bulbs()
    if name == "all":
        targets = bulbs.values()
    else:
        targets = [bulbs.get(name)]
    
    for b in targets:
        if b:
            wl = wizlight(b["ip"], 38899, b["mac"])
            if color == "off":
                await wl.turn_off()
            else:
                rgb = COLORS.get(color, (255, 255, 255))
                await wl.turn_on(PilotBuilder(rgb=rgb, brightness=brightness))
            print(f"{b['ip']} -> {color}")

async def main():
    if len(sys.argv) < 3:
        print("Usage: wiz-lights.py <light-name> <color>")
        print("Example: wiz-lights.py all cyan")
        sys.exit(1)
    
    light = sys.argv[1]
    color = sys.argv[2]
    await set_light(light, color)

if __name__ == "__main__":
    asyncio.run(main())