#!/usr/bin/env python3
from __future__ import annotations
import ast
import os
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(".").resolve()
MAIN = ROOT / "main.py"

WANT = {
  "initSymbol": ("exchange/symbols.py", "exchange.symbols"),
  "quantDown": ("execution/sizing.py", "execution.sizing"),
  "usdcFree": ("execution/sizing.py", "execution.sizing"),
}

TARGET_DIRS = [
  "core","services","exchange","indicators","strategy","execution","state","runtime"
]

def read_text(p: Path) -> str:
  return p.read_text(encoding="utf-8")

def write_text(p: Path, s: str) -> None:
  p.parent.mkdir(parents=True, exist_ok=True)
  p.write_text(s, encoding="utf-8")

def ensure_inits() -> None:
  for d in TARGET_DIRS:
    (ROOT / d).mkdir(parents=True, exist_ok=True)
    initp = ROOT / d / "__init__.py"
    if not initp.exists():
      write_text(initp, "")

def parse_main(src: str) -> ast.Module:
  return ast.parse(src)

def get_func_segments(src: str, tree: ast.Module) -> Dict[str, Tuple[int,int,str]]:
  lines = src.splitlines(keepends=True)
  out: Dict[str, Tuple[int,int,str]] = {}
  for node in tree.body:
    if isinstance(node, ast.FunctionDef) and node.name in WANT:
      if not hasattr(node, "lineno") or not hasattr(node, "end_lineno"):
        continue
      a = node.lineno - 1
      b = node.end_lineno
      seg = "".join(lines[a:b])
      out[node.name] = (a, b, seg)
  return out

def build_module_header() -> str:
  return (
    "# auto-extracted from main.py\n"
    "from __future__ import annotations\n"
    "from decimal import Decimal\n"
    "from typing import Any, Tuple\n\n"
  )

def append_funcs_to_targets(funcs: Dict[str, Tuple[int,int,str]], main_src: str) -> Dict[str, List[str]]:
  per_file: Dict[str, List[str]] = {}
  for name, (_, _, seg) in funcs.items():
    path, _ = WANT[name]
    per_file.setdefault(path, []).append(seg.rstrip() + "\n\n")
  for path, chunks in per_file.items():
    target = ROOT / path
    if target.exists():
      existing = read_text(target)
      out = existing
      if out and not out.endswith("\n"):
        out += "\n"
    else:
      out = build_module_header()
    out += "".join(chunks)
    write_text(target, out)
  return per_file

def patch_main_remove_defs(src: str, funcs: Dict[str, Tuple[int,int,str]]) -> str:
  if not funcs:
    return src
  lines = src.splitlines(keepends=True)
  spans = sorted([(a,b) for (a,b,_) in funcs.values()], key=lambda x: x[0], reverse=True)
  for a,b in spans:
    del lines[a:b]
  return "".join(lines)

def patch_main_imports(src: str, extracted: List[str]) -> str:
  if not extracted:
    return src

  want_imports: Dict[str, List[str]] = {}
  for fn in extracted:
    _, mod = WANT[fn]
    want_imports.setdefault(mod, []).append(fn)

  import_lines = []
  for mod, fns in want_imports.items():
    fns_sorted = ", ".join(sorted(set(fns)))
    import_lines.append(f"from {mod} import {fns_sorted}\n")

  add_block = "".join(import_lines) + "\n"

  lines = src.splitlines(keepends=True)

  # insert after last import (or after shebang/encoding header)
  insert_at = 0
  for i, ln in enumerate(lines):
    s = ln.strip()
    if s.startswith("import ") or s.startswith("from "):
      insert_at = i + 1
    elif i < 2 and (s.startswith("#!") or "coding" in s):
      insert_at = i + 1

  already = src
  for ln in import_lines:
    if ln.strip() in already:
      add_block = add_block.replace(ln, "")

  if add_block.strip() == "":
    return src

  lines.insert(insert_at, add_block)
  return "".join(lines)

def main() -> int:
  if not MAIN.exists():
    print("ERR: main.py introuvable (lance depuis la racine du projet).")
    return 2

  ensure_inits()

  src = read_text(MAIN)
  tree = parse_main(src)
  funcs = get_func_segments(src, tree)

  if not funcs:
    print("Rien à extraire: fonctions non trouvées (initSymbol, quantDown, usdcFree).")
    return 0

  per_file = append_funcs_to_targets(funcs, src)

  new_src = patch_main_remove_defs(src, funcs)
  new_src = patch_main_imports(new_src, list(funcs.keys()))

  bak = MAIN.with_suffix(".py.bak")
  if not bak.exists():
    write_text(bak, src)

  write_text(MAIN, new_src)

  print("OK:")
  for path, chunks in per_file.items():
    print(f" - wrote {path}: {len(chunks)} func(s)")
  print(" - patched main.py")
  print(" - backup main.py.bak")
  return 0

if __name__ == "__main__":
  raise SystemExit(main())
