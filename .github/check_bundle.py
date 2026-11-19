import ast
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
ENTRY = "rdb"


def needed():
    local = {p.stem for p in SRC.glob("*.py")}
    std = set(sys.stdlib_module_names)
    out = set(local - {ENTRY})
    for p in SRC.glob("*.py"):
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(n, ast.Import):
                names = [a.name for a in n.names]
            elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
                names = [n.module]
            else:
                continue
            out |= {x.split(".")[0] for x in names} - std
    return out


def bundled(exe):
    listing = subprocess.run(
        ["pyi-archive_viewer", "-r", "-b", str(exe)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    names = {line.strip() for line in listing.splitlines()}
    return {n.split(".")[0] for n in names} | {n.split("\\")[0] for n in names}


def main():
    exe = ROOT / "dist" / "rdb.exe"
    missing = sorted(needed() - bundled(exe))
    if missing:
        sys.exit(f"{exe} is missing modules: {', '.join(missing)}")
    print(f"{exe}: all {len(needed())} imported modules bundled")


if __name__ == "__main__":
    main()
