#!/usr/bin/env python3
"""Add cross-module private helper imports after mechanical view split."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def helper_names(pkg_dir: Path) -> set[str]:
    helpers = pkg_dir / "_helpers.py"
    tree = ast.parse(helpers.read_text(encoding="utf-8"))
    return {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}


def used_private_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    defined = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id.startswith("_") and node.id not in defined:
            used.add(node.id)
    return used


def inject_imports(path: Path, names: set[str]) -> None:
    if not names:
        return
    text = path.read_text(encoding="utf-8")
    if ".views._helpers import" in text:
        return
    parts = path.parts
    app = parts[parts.index("apps") + 1]
    import_block = (
        f"from apps.{app}.views._helpers import (\n    "
        + ",\n    ".join(sorted(names))
        + ",\n)\n"
    )
    tree = ast.parse(text)
    insert_line = 0
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            insert_line = node.end_lineno
    lines = text.splitlines(keepends=True)
    lines.insert(insert_line, "\n" + import_block)
    path.write_text("".join(lines), encoding="utf-8")


def fix_pkg(app: str) -> None:
    pkg = ROOT / "apps" / app / "views"
    helpers = helper_names(pkg)
    for path in pkg.glob("*.py"):
        if path.name in ("__init__.py", "_helpers.py"):
            continue
        need = used_private_names(path) & helpers
        inject_imports(path, need)


def main() -> None:
    fix_pkg("pos")
    fix_pkg("purchasing")
    print("Cross-imports added")


if __name__ == "__main__":
    main()
