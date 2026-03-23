import argparse
import sys

import bench_intercode
import security_bench
import test_bench


MODE_HANDLERS = {
    "intercode": bench_intercode.main,
    "security": security_bench.main,
    "test": test_bench.main,
}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Experiment entry point")
    parser.add_argument(
        "--mode",
        choices=sorted(MODE_HANDLERS),
        required=True,
        help="Benchmark mode to run",
    )
    parser.add_argument(
        "mode_args",
        nargs=argparse.REMAINDER,
        help="Arguments forwarded to the selected mode",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    handler = MODE_HANDLERS[args.mode]
    forwarded_args = list(args.mode_args)
    if forwarded_args and forwarded_args[0] == "--":
        forwarded_args = forwarded_args[1:]
    return handler(forwarded_args)


if __name__ == "__main__":
    sys.exit(main())
