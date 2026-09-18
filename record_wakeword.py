#!/usr/bin/env python3
"""
Record wake-word training clips for a custom wake phrase.

Records N short WAV clips (16kHz mono 16-bit) of the user saying the
wake phrase, plus N clips of other speech (negatives). Saves to
~/wakeword-training/positive/ and ~/wakeword-training/negative/.

Usage:
  python3 record_wakeword.py positive 10   # 10 clips of your wake phrase
  python3 record_wakeword.py negative 10   # 10 clips of other speech
"""
import os
import sys
import time

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
CLIP_SECONDS = 1.6
OUT_DIR = os.path.expanduser("~/wakeword-training")


def record_clip(seconds):
    print(f"  recording {seconds}s...", flush=True)
    audio = sd.rec(int(SAMPLE_RATE * seconds), samplerate=SAMPLE_RATE, channels=1, dtype="int16")
    sd.wait()
    return audio.flatten()


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    kind = sys.argv[1]  # 'positive' or 'negative'
    count = int(sys.argv[2])

    if kind not in ("positive", "negative"):
        print("kind must be 'positive' or 'negative'")
        sys.exit(1)

    subdir = os.path.join(OUT_DIR, kind)
    os.makedirs(subdir, exist_ok=True)

    import wave
    for i in range(count):
        print(f"\nClip {i+1}/{count} — ", end="", flush=True)
        if kind == "positive":
            print("say your wake phrase after the beep", flush=True)
        else:
            print("say something else (not the wake phrase) after the beep", flush=True)

        # beep
        beep = (np.sin(2 * np.pi * 880 * np.arange(SAMPLE_RATE // 4) / SAMPLE_RATE) * 0.3).astype("int16")
        sd.play(beep, SAMPLE_RATE)
        sd.wait()

        audio = record_clip(CLIP_SECONDS)

        path = os.path.join(subdir, f"{kind}_{i:03d}.wav")
        with wave.open(path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(audio.tobytes())
        print(f"  saved {path}", flush=True)

    print(f"\nDone. {count} clips saved to {subdir}", flush=True)


if __name__ == "__main__":
    main()
