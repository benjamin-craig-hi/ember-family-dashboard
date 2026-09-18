#!/usr/bin/env python3
"""Test the Ember camera tool loop + vision attachment WITHOUT the wake word.

Imports voice_assistant's describe() path directly and feeds it text, so we
can verify tool selection, the frame capture, and that the model really sees
the image -- all without speaking to the kiosk.
"""
import json
import sys

import voice_assistant as va
import llm

# Force a vision-capable model regardless of current settings.
settings = va._load_settings()
settings["llm_provider"] = "api"
settings["llm_api_provider"] = "ollama"
settings["llm_model"] = "deepseek-v4.1-flash"

# Patch _load_settings so describe() picks up our override.
va._load_settings = lambda: settings

QUERIES = [
    "What is on the cameras right now?",
    "Has anyone come to the door today?",
    "What can you see in the living room?",
]

print("=" * 70)
print("EMBER CAMERA TOOL TEST  (model:", settings["llm_model"], ")")
print("=" * 70)

for q in QUERIES:
    print(f"\n>>> USER: {q}")
    try:
        ans = va.ask_llm(q)
        print(f"<<< EMBER: {ans}")
    except Exception as e:
        import traceback
        print(f"!!! ERROR: {type(e).__name__}: {e}")
        traceback.print_exc()
