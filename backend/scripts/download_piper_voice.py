"""Download a Piper voice (model + config) from the rhasspy/piper-voices repository.

    python scripts/download_piper_voice.py en_US-lessac-medium voices
"""
import pathlib
import sys
import urllib.request

name = sys.argv[1] if len(sys.argv) > 1 else "en_US-lessac-medium"
dest = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else "voices")
dest.mkdir(parents=True, exist_ok=True)

lang_region, voice, quality = name.split("-")  # e.g. en_US, lessac, medium
lang = lang_region.split("_")[0]
base = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/{lang}/{lang_region}/{voice}/{quality}/{name}"

for suffix in (".onnx", ".onnx.json"):
    target = dest / f"{name}{suffix}"
    if target.exists() and target.stat().st_size > 0:
        print("already present:", target)
        continue
    print("downloading", base + suffix)
    urllib.request.urlretrieve(base + suffix, target)
    print("saved", target, target.stat().st_size, "bytes")
