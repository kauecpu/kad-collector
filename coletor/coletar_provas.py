import sys

from kad_collector.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["collect", *sys.argv[1:]]))
