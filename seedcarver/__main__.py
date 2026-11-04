"""Command line: `python -m seedcarver [app | build N | bench]`."""
import argparse
import os
import time

from . import bank, core
from .paths import BANK


def bench():
    n, path = 100_000, f"{BANK}.bench"  # enough blocks to load every thread
    start = time.perf_counter()
    bank.build(path, n)
    rate = n / (time.perf_counter() - start)
    os.remove(path)
    print(f"{rate:,.0f} seeds/s on {bank.THREADS} threads")
    for size in (1_000_000, 10_000_000, 100_000_000):
        print(f"{size:>12,} seeds: {size / rate / 60:6.1f} min, {size * core.CELLS / 1e9:5.1f} GB")


def main():
    parser = argparse.ArgumentParser(prog="seedcarver")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("app", help="open the desktop app (default)")
    sub.add_parser("bench", help="time 100k seeds and estimate bank build times")
    build = sub.add_parser("build", help="grow the seed bank to N seeds")
    build.add_argument("seeds", type=int)
    args = parser.parse_args()
    if args.cmd == "build":
        started, first = time.perf_counter(), bank.count(BANK)

        def show(done, target):
            rate = (done - first) / (time.perf_counter() - started)
            print(f"\r{done:,}/{target:,} ({rate:,.0f}/s)", end="", flush=True)

        bank.build(BANK, args.seeds, show)
        print()
    elif args.cmd == "bench":
        bench()
    else:
        from .app import run
        run()


if __name__ == "__main__":
    main()
