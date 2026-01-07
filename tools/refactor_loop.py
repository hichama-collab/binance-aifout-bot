#!/usr/bin/env python3
from __future__ import annotations
import re
from pathlib import Path

ROOT = Path(".").resolve()
MAIN = ROOT / "main.py"
OUT  = ROOT / "runtime" / "loop.py"

def read(p: Path) -> str:
  return p.read_text(encoding="utf-8")

def write(p: Path, s: str) -> None:
  p.parent.mkdir(parents=True, exist_ok=True)
  p.write_text(s, encoding="utf-8")

LOOP_HEADER = """# extracted runtime loop (state machine)
from __future__ import annotations
import time
from typing import Any, Optional

def run_loop(
  *,
  bx: Any,
  stream: Any,
  strat: Any,
  cfg: Any,
  symbol: str,
  spec: Any,
  walletSyncEvery: Any,
  placeLimit: Any,
  waitFillOrCancel: Any,
) -> None:
"""

def indent_block(block: str, n: int = 2) -> str:
  pad = " " * n
  return "".join(pad + ln if ln.strip() != "" else ln for ln in block.splitlines(keepends=True))

def main() -> int:
  if not MAIN.exists():
    print("ERR: main.py introuvable.")
    return 2

  src = read(MAIN)
  bak = MAIN.with_suffix(".py.loop.bak")
  if not bak.exists():
    write(bak, src)

  # Find the main while True loop block by locating "while True:" and taking until end of file.
  m = re.search(r"\n(\s*)while\s+True\s*:\s*\n", src)
  if not m:
    print("ERR: while True: non trouvé dans main.py (pattern).")
    return 3

  start = m.start()
  loop_block = src[start:]

  # Remove the loop from main.py
  src_no_loop = src[:start]

  # Ensure import in main.py
  if "from runtime.loop import run_loop" not in src_no_loop:
    # insert after imports
    lines = src_no_loop.splitlines(keepends=True)
    insert_at = 0
    for i, ln in enumerate(lines):
      s = ln.strip()
      if s.startswith("import ") or s.startswith("from "):
        insert_at = i + 1
      elif i < 2 and (s.startswith("#!") or "coding" in s):
        insert_at = i + 1
    lines.insert(insert_at, "from runtime.loop import run_loop\n\n")
    src_no_loop = "".join(lines)

  # Build runtime/loop.py content: wrap extracted block into run_loop()
  # Convert "while True:" into function body with same indentation.
  loop_block = loop_block.lstrip("\n")
  # Indent the entire original loop by 2 spaces so it sits under def run_loop(...):
  loop_body = indent_block(loop_block, 2)

  out_loop = LOOP_HEADER + loop_body
  write(OUT, out_loop)

  # Patch main.py to call run_loop(...) where the while loop was
  call = (
    "\n  # runtime loop extracted\n"
    "  run_loop(\n"
    "    bx=bx,\n"
    "    stream=stream,\n"
    "    strat=strat,\n"
    "    cfg=cfg,\n"
    "    symbol=symbol,\n"
    "    spec=spec,\n"
    "    walletSyncEvery=walletSyncEvery,\n"
    "    placeLimit=placeLimit,\n"
    "    waitFillOrCancel=waitFillOrCancel,\n"
    "  )\n"
  )

  # Insert call just before end of main() (before any final return or end)
  # Simple: append at end of src_no_loop
  if not src_no_loop.endswith("\n"):
    src_no_loop += "\n"
  src_no_loop += call

  write(MAIN, src_no_loop)

  print("OK:")
  print(" - wrote runtime/loop.py (extracted while loop)")
  print(" - patched main.py (replaced while loop with run_loop call)")
  print(" - backup main.py.loop.bak")
  return 0

if __name__ == "__main__":
  raise SystemExit(main())
