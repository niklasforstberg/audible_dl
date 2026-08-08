"""Checks that every downloaded book matches its expected size on the server.

Reports OK / MISMATCH / MISSING / INCOMPLETE for each book in the library.
Downloads nothing — only reads the size from the server. Run after download.py.

Usage: python check.py [BOOKS_DIR]   (BOOKS_DIR defaults to ~/books)
"""
import argparse
import logging
import os
import random
import shlex
import time
from pathlib import Path

import audible

from download import (
    AUTH_FILE,
    book_stem,
    expected_size,
    find_local,
    find_part,
    get_library,
)

DEFAULT_BOOKS_DIR = Path.home() / "books"
DEFAULT_LOG_FILE = "check.log"

log = logging.getLogger("check")


def setup_logging(log_file):
    """Log to the console (plain) and to a file (with timestamps)."""
    log.setLevel(logging.INFO)

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(console)

    file = logging.FileHandler(log_file, mode="w", encoding="utf-8")
    file.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    log.addHandler(file)

# Small polite pause between the metadata calls (seconds).
MIN_DELAY = 1
MAX_DELAY = 3


def quoted(path):
    """Full path, shell-quoted so it can be pasted straight into a terminal
    (book filenames contain spaces and [ASIN] brackets)."""
    return shlex.quote(str(path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "books_dir",
        nargs="?",
        default=str(DEFAULT_BOOKS_DIR),
        help="folder with the downloaded books (default: ~/books)",
    )
    parser.add_argument(
        "--log",
        default=DEFAULT_LOG_FILE,
        help=f"file to append the log to (default: {DEFAULT_LOG_FILE})",
    )
    args = parser.parse_args()
    books_dir = Path(args.books_dir)

    setup_logging(args.log)

    if not os.path.exists(AUTH_FILE):
        raise SystemExit("No auth.json — run auth.py first.")

    auth = audible.Authenticator.from_file(AUTH_FILE)
    counts = {"ok": 0, "mismatch": 0, "missing": 0, "incomplete": 0, "unknown": 0}

    with audible.Client(auth=auth) as client:
        library = get_library(client)
        log.info(f"Checking {len(library)} books in {books_dir} ...")

        for i, item in enumerate(library, 1):
            asin = item["asin"]
            title = item.get("title", asin)
            stem = book_stem(item)
            prefix = f"[{i}/{len(library)}]"

            local = find_local(books_dir, stem)
            if local is None:
                part = find_part(books_dir, stem)
                if part is not None:
                    log.info(f"{prefix} INCOMPLETE (.part only): {quoted(part)}")
                    counts["incomplete"] += 1
                else:
                    # Nothing on disk to point at, so name the book instead.
                    log.info(f"{prefix} MISSING: {title} [{asin}]")
                    counts["missing"] += 1
                continue

            try:
                exp = expected_size(client, asin)
            except Exception as e:
                log.info(f"{prefix} ERROR checking {quoted(local)}: {e}")
                counts["unknown"] += 1
            else:
                actual = local.stat().st_size
                if exp is None:
                    log.info(f"{prefix} UNKNOWN (server gave no size): {quoted(local)} — local {actual} bytes")
                    counts["unknown"] += 1
                elif actual == exp:
                    log.info(f"{prefix} OK: {quoted(local)} ({actual} bytes)")
                    counts["ok"] += 1
                else:
                    log.info(f"{prefix} MISMATCH: {quoted(local)} — local {actual}, expected {exp}")
                    counts["mismatch"] += 1

            if i < len(library):
                time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

    log.info(
        f"OK: {counts['ok']}  Mismatch: {counts['mismatch']}  "
        f"Missing: {counts['missing']}  Incomplete: {counts['incomplete']}  "
        f"Unknown: {counts['unknown']}"
    )


if __name__ == "__main__":
    main()
