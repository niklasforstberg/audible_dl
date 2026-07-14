"""Decodes downloaded .aax files into DRM-free .m4b using ffmpeg.

Run download.py first. Requires ffmpeg in PATH.

Usage: python decode.py [BOOKS_DIR]   (BOOKS_DIR defaults to ~/books)
"""
import argparse
import subprocess
from pathlib import Path

import audible

AUTH_FILE = "auth.json"
DEFAULT_BOOKS_DIR = Path.home() / "books"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "books_dir",
        nargs="?",
        default=str(DEFAULT_BOOKS_DIR),
        help="folder with .aax files (default: ~/books)",
    )
    args = parser.parse_args()
    books_dir = Path(args.books_dir)

    auth = audible.Authenticator.from_file(AUTH_FILE)
    activation_bytes = auth.get_activation_bytes()

    aax_files = sorted(books_dir.glob("*.aax"))
    if not aax_files:
        raise SystemExit(f"No .aax files in {books_dir} — run download.py first.")

    print(f"Found {len(aax_files)} files to decode.")
    for i, aax in enumerate(aax_files, 1):
        out = aax.with_suffix(".m4b")
        print(f"[{i}/{len(aax_files)}] {aax.name}")
        if out.exists():
            print(f"  skipping (already exists): {out.name}")
            continue

        result = subprocess.run(
            [
                "ffmpeg",
                "-activation_bytes", activation_bytes,
                "-i", str(aax),
                "-c", "copy",
                str(out),
            ],
            capture_output=True,
            text=True,
            errors="replace",
        )
        if result.returncode == 0:
            print(f"  done: {out.name}")
        else:
            print(f"  error: {result.stderr.strip().splitlines()[-1]}")
            out.unlink(missing_ok=True)  # remove partial file

    print("Done.")


if __name__ == "__main__":
    main()
