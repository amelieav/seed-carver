"""Command line: `python -m seedcarver [app | build N | bench | convert]`."""
import argparse
import os
import time

from . import bank, core
from .paths import BANK


def bench():
    n, path = 10_000, f"{BANK}.bench"
    start = time.perf_counter()
    bank.build(path, n)
    rate = n / (time.perf_counter() - start)
    os.remove(path)
    print(f"{rate:,.0f} seeds/s on {bank.THREADS} threads")
    for size in (100_000, 1_000_000, 10_000_000):
        print(f"{size:>12,} seeds: {size / rate / 60:6.1f} min, {size * bank.SEED_BYTES / 1e9:5.1f} GB")


def main():
    parser = argparse.ArgumentParser(prog="seedcarver")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("app", help="open the desktop app (default)")
    sub.add_parser("bench", help="time 10k seeds and estimate bank build times")
    conv = sub.add_parser("convert", help="shrink a previous-version seed bank to the compressed format, in place")
    conv.add_argument("path", nargs="?", default=str(BANK), help="the bank file (default: seeds.bank beside the app)")
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
    elif args.cmd == "convert":
        before = os.path.getsize(args.path)
        bank.convert(args.path, lambda done, total: print(f"\r{done:,}/{total:,}", end="", flush=True))
        print(f"\n{before / 1e9:.1f} GB -> {os.path.getsize(args.path) / 1e9:.1f} GB")
    elif args.cmd == "bench":
        bench()
    else:
        from .app import run
        run()


if __name__ == "__main__":
    main()
