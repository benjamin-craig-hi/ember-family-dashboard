#!/usr/bin/env python3
"""Test development mode through the real voice-assistant entrypoint.

Verifies: dev tools are hidden when the setting is off, offered when on, and
that a natural spoken instruction ("change X") actually lands as a self-edit.
"""
import json
import os
import sys

import voice_assistant as va
import devmode
import llm

settings = va._load_settings()
settings["llm_provider"] = "api"
settings["llm_api_provider"] = "ollama"
settings["llm_model"] = "deepseek-v4.1-flash"
_orig = va._load_settings
va._load_settings = lambda: settings

fmt = llm.tool_format(settings)


def tool_names(active):
    return [t["function"]["name"] for t in active]


print("=" * 70)
print("DEV MODE TEST")
print("=" * 70)

print("\n--- OFF: dev tools must not be offered ---")
settings["dev_mode"] = False
active = va.TOOLS + (va.DEV_TOOLS if devmode.is_enabled(settings) else [])
names = tool_names(active)
print(f"  dev tools offered: {[n for n in names if n.startswith('dev_')] or 'NONE (correct)'}")

print("\n--- ON: dev tools must be offered ---")
settings["dev_mode"] = True
active = va.TOOLS + (va.DEV_TOOLS if devmode.is_enabled(settings) else [])
names = tool_names(active)
devnames = [n for n in names if n.startswith("dev_")]
print(f"  dev tools offered: {devnames}")

print("\n--- ON: does a spoken instruction become a self-edit? ---")
Q = ("In development mode, read static/meals.html and change the meal plan "
     "heading so it says 'Family Meals' instead of 'Meal Plan'. Then tell me "
     "the service status.")
print(f">>> USER: {Q}")
try:
    ans = va.ask_llm(Q)
    print(f"<<< EMBER: {ans}")
except Exception as e:
    import traceback
    print(f"!!! {type(e).__name__}: {e}")
    traceback.print_exc()

print("\n--- resulting file state ---")
txt = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "static/meals.html")).read()
print("  contains 'Family Meals':", "Family Meals" in txt)
print("  contains 'Meal Plan'   :", "Meal Plan" in txt)
print("  recent commits:")
print("   ", devmode.recent_changes(3).replace("\n", "\n    "))
print("\n--- services ---")
print("  ", devmode.status())

va._load_settings = _orig
