#!/usr/bin/env python3
"""Sync the Longshu 8MB full-upstream firmware into the author's updater payload.

Run from repository root after:
    cd hardware
    pio run -e longshu_xiaowai_full
    cd ..
    python service/scripts/sync_longshu_firmware_payload.py

The Web console still expects the canonical payload file name deskbot_v2.bin;
we intentionally keep that contract while replacing its bytes with the Longshu
8MB build.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VERSION_FILE = ROOT / "hardware" / "VERSION"
BUILT = ROOT / "hardware" / ".pio" / "build" / "longshu_xiaowai_full" / "firmware.bin"
PAYLOAD_DIR = ROOT / "service" / "src" / "deskbot_server" / "firmware_payload"
PAYLOAD_BIN = PAYLOAD_DIR / "deskbot_v2.bin"
MANIFEST = PAYLOAD_DIR / "manifest.json"


def main() -> int:
    version = VERSION_FILE.read_text(encoding="utf-8").strip()
    if not BUILT.is_file():
        print(f"未找到构建产物: {BUILT}", file=sys.stderr)
        print("请先: cd hardware && pio run -e longshu_xiaowai_full", file=sys.stderr)
        return 1
    if version.encode("utf-8") not in BUILT.read_bytes():
        print("构建产物不包含当前版本；拒绝同步旧固件，请重新编译。", file=sys.stderr)
        return 1
    PAYLOAD_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(BUILT, PAYLOAD_BIN)
    data = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.is_file() else {}
    data.update(
        version=version,
        build_required=False,
        file="deskbot_v2.bin",
        chip="esp32s3",
        flash_offset="0x10000",
        build_profile="longshu_xiaowai_full",
        flash_size="8MB",
    )
    MANIFEST.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Longshu firmware {version}: {BUILT.stat().st_size} bytes -> {PAYLOAD_BIN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
