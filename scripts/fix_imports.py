import re
from pathlib import Path

ROUTERS_DIR = Path("rigapp/app/routers")

def process_file(file_path: Path):
    with open(file_path, "r") as f:
        lines = f.readlines()

    changed = False
    new_lines = []

    # Remove existing __future__ imports to relocate them
    future_lines = [i for i, l in enumerate(lines) if l.strip().startswith("from __future__")]
    if future_lines:
        for i in reversed(future_lines):
            del lines[i]
        changed = True

    # Insert __future__ at very top
    new_lines.append("from __future__ import annotations\n")

    # Insert page_auto if not already there
    if not any("from ..ui import page_auto" in l for l in lines):
        new_lines.append("from ..ui import page_auto\n")
        changed = True

    # Add back the rest of the file
    new_lines.extend(lines)

    # Replace HTMLResponse(html) calls
    for i, line in enumerate(new_lines):
        if "HTMLResponse(html)" in line:
            new_lines[i] = line.replace("HTMLResponse(html)", "page_auto(html)")
            changed = True

    if changed:
        with open(file_path, "w") as f:
            f.writelines(new_lines)
        print(f"Updated: {file_path}")
    else:
        print(f"No changes: {file_path}")

def main():
    for py_file in ROUTERS_DIR.glob("*.py"):
        process_file(py_file)

if __name__ == "__main__":
    main()
