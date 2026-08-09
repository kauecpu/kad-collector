import sys

from kad_collector.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["stage", *sys.argv[1:]]))
