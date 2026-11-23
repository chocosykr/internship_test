#!/usr/bin/env python3
"""CLI utility to ingest plain-text log files (.txt, .log) and convert them to structured JSON (data/logs.json).

Usage:
  python ingest_logs.py                              # Converts backend/data/logs.txt -> backend/data/logs.json
  python ingest_logs.py server.log                   # Ingests server.log -> backend/data/logs.json
  python ingest_logs.py --output custom.json app.log # Ingests into custom output file
  python ingest_logs.py --service payment-service raw.txt
  python ingest_logs.py --merge new_logs.txt         # Merges with existing logs.json
"""
import argparse
import os
import sys

# Ensure backend directory is in sys.path
backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import text_io

DEFAULT_DATA_DIR = os.path.join(backend_dir, "data")
DEFAULT_TXT = os.path.join(DEFAULT_DATA_DIR, "logs.txt")
DEFAULT_JSON = os.path.join(DEFAULT_DATA_DIR, "logs.json")


def main():
    parser = argparse.ArgumentParser(
        description="Convert plain-text log files (.txt, .log) into structured JSON for the Incident Triage Agent."
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        help="One or more text log files or directories to ingest (default: backend/data/logs.txt)",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=DEFAULT_JSON,
        help=f"Target JSON file path (default: {DEFAULT_JSON})",
    )
    parser.add_argument(
        "-s",
        "--service",
        default="",
        help="Default service name to assign if missing in log entries",
    )
    parser.add_argument(
        "-m",
        "--merge",
        action="store_true",
        help="Merge new logs with existing JSON output rather than replacing",
    )

    args = parser.parse_args()

    # Determine input sources
    inputs = args.inputs
    if not inputs:
        if os.path.exists(DEFAULT_TXT):
            inputs = [DEFAULT_TXT]
        else:
            alt = os.path.join(DEFAULT_DATA_DIR, "logs.log")
            if os.path.exists(alt):
                inputs = [alt]
            else:
                print(f"Error: No log files specified and default '{DEFAULT_TXT}' was not found.", file=sys.stderr)
                print("Usage: python ingest_logs.py <path_to_log_file>", file=sys.stderr)
                sys.exit(1)

    print(f"Ingesting plain-text logs from: {', '.join(inputs)}")
    try:
        entries = text_io.ingest_log_sources(
            sources=inputs,
            output_json=args.output,
            merge=args.merge,
            default_service=args.service,
        )
    except Exception as e:
        print(f"Failed to ingest logs: {e}", file=sys.stderr)
        sys.exit(1)

    services = sorted({e.get("service") for e in entries if e.get("service")})
    levels = {}
    for e in entries:
        lvl = e.get("level", "UNKNOWN")
        levels[lvl] = levels.get(lvl, 0) + 1

    level_summary = ", ".join(f"{k}: {v}" for k, v in sorted(levels.items()))

    print(f"\n[OK] Successfully converted {len(entries)} log entries -> {args.output}")
    print(f"     Services: {', '.join(services) if services else 'none specified'}")
    print(f"     Levels:   {level_summary}")


if __name__ == "__main__":
    main()
