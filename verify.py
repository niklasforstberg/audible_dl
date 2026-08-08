"""Checks that decoded .m4b files actually contain playable audio.

A book decoded with the wrong key produces a file that looks perfectly fine —
right size, right duration, right chapters — but whose audio is still encrypted
garbage. Only decoding it reveals that. This script decodes the first stretch of
each .m4b and reports the ones ffmpeg chokes on.

Needs nothing but ffmpeg: no auth.json, no voucher, no network. Run it on the
machine holding the books.

Usage: python verify.py [BOOKS_DIR]   (BOOKS_DIR defaults to ~/books)
"""
import argparse
import logging
import shlex
import subprocess
from pathlib import Path

DEFAULT_BOOKS_DIR = Path.home() / "books"
DEFAULT_LOG_FILE = "verify.log"
DEFAULT_SECONDS = 30

# ffmpeg's complaints when it is handed audio it cannot make sense of. Warnings
# about odd-but-decodable streams (e.g. "Number of bands exceeds limit") are not
# listed here on purpose — they show up on healthy files too.
ERROR_MARKERS = (
    "Invalid data found",
    "Error submitting packet",
    "Error while decoding",
)

log = logging.getLogger("verify")


def setup_logging(log_file):
    """Log to the console (plain) and to a file (with timestamps)."""
    log.setLevel(logging.INFO)

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(console)

    file = logging.FileHandler(log_file, mode="w", encoding="utf-8")
    file.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    log.addHandler(file)


def quoted(path):
    """Full path, shell-quoted so it can be pasted straight into a terminal."""
    return shlex.quote(str(path))


def decode_errors(book, seconds):
    """Decode the first `seconds` of audio and throw the output away.
    Returns (error_count, first_error_line)."""
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(book), "-t", str(seconds),
         "-map", "0:a:0", "-f", "null", "-"],
        capture_output=True, text=True, errors="replace",
    )
    bad = [
        line for line in result.stderr.splitlines()
        if any(marker in line for marker in ERROR_MARKERS)
    ]
    return len(bad), (bad[0].strip() if bad else "")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "books_dir",
        nargs="?",
        default=str(DEFAULT_BOOKS_DIR),
        help="folder with the decoded .m4b files (default: ~/books)",
    )
    parser.add_argument(
        "--seconds",
        type=int,
        default=DEFAULT_SECONDS,
        help=f"how much audio to test-decode per book (default: {DEFAULT_SECONDS})",
    )
    parser.add_argument(
        "--log",
        default=DEFAULT_LOG_FILE,
        help=f"file to write the log to (default: {DEFAULT_LOG_FILE})",
    )
    args = parser.parse_args()
    books_dir = Path(args.books_dir)

    setup_logging(args.log)

    books = sorted(books_dir.glob("*.m4b"))
    if not books:
        raise SystemExit(f"No .m4b files in {books_dir} — run decode.py first.")

    log.info(f"Verifying {len(books)} books in {books_dir} ({args.seconds}s each) ...")
    broken = []

    for i, book in enumerate(books, 1):
        prefix = f"[{i}/{len(books)}]"
        count, first = decode_errors(book, args.seconds)
        if count:
            log.info(f"{prefix} BROKEN: {quoted(book)} — {count} decode errors, e.g. {first}")
            broken.append(book)
        else:
            log.info(f"{prefix} OK: {quoted(book)}")

    log.info(f"\nOK: {len(books) - len(broken)}  Broken: {len(broken)}")
    if broken:
        # Printed as one block so the whole list can be pasted into a shell.
        log.info("\nDelete these and run decode.py again:\n")
        log.info("rm " + " ".join(quoted(b) for b in broken))


if __name__ == "__main__":
    main()
