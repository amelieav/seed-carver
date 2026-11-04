"""Compile the C core. `python build.py --exe` also packages a standalone app."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
VENDOR = ROOT / "vendor" / "cubiomes"
EXT = {"win32": "dll", "darwin": "dylib"}.get(sys.platform, "so")
LIB = ROOT / "seedcarver" / f"core.{EXT}"
SOURCES = ["biomenoise", "biomes", "noise", "util", "generator", "layers"]


def compile_core():
    if not (VENDOR / "biomenoise.c").exists():
        subprocess.check_call(["git", "submodule", "update", "--init"], cwd=ROOT)
    link = ["-static"] if sys.platform == "win32" else ["-fPIC"]
    subprocess.check_call(
        ["gcc", "-O3", "-fwrapv", "-shared", *link, f"-I{VENDOR}", str(ROOT / "core" / "seedcarver.c")]
        + [str(VENDOR / f"{s}.c") for s in SOURCES]
        + ["-lm", "-o", str(LIB)]
    )


def package():
    sep = ";" if sys.platform == "win32" else ":"
    subprocess.check_call([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--windowed", "--name", "SeedCarver",
        "--add-binary", f"{LIB}{sep}seedcarver",
        "--add-data", f"{ROOT / 'seedcarver' / 'ui.html'}{sep}seedcarver",
        str(ROOT / "run.py"),
    ], cwd=ROOT)


if __name__ == "__main__":
    compile_core()
    if "--exe" in sys.argv:
        package()
