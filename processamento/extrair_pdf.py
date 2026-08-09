import sys

from kad_collector.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["extract", *sys.argv[1:]]))
