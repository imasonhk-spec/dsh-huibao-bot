"""Copy prior LLM settings only; preserve this package's ASR/TTS configuration."""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# This migration always writes this copy, never a path left by an old service.
os.environ["DESKBOT_ENV_FILE"] = str(ROOT / ".env")
sys.path.insert(0, str(ROOT / "src"))
from deskbot_server.env import update_env_keys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("previous_env", type=Path)
    args = parser.parse_args()
    source = args.previous_env.resolve()
    destination = ROOT / ".env"
    if source == destination.resolve():
        parser.error("Source and destination must be different.")
    values = {}
    for line in source.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.strip().removeprefix("export ").split("=", 1)
        key, value = key.strip(), value.strip()
        if key.startswith("LLM_") and value:
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[key] = value
    update_env_keys(values, keys=values.keys(), comment="# Imported previous LLM configuration")
    print(f"Imported {len(values)} LLM settings; speech settings unchanged. No keys displayed.")


if __name__ == "__main__":
    main()
