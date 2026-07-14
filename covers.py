"""Extracts the embedded cover image from each .m4b into <book_name>.jpg.

Run decode.py first. Requires ffmpeg in PATH.

Usage: python covers.py [BOOKS_DIR]   (BOOKS_DIR defaults to ~/books)
"""
import argparse
import subprocess
from pathlib import Path

DEFAULT_BOOKS_DIR = Path.home() / "books"

# ffprobe codec_name -> cover file extension
CODEC_EXT = {"mjpeg": ".jpg", "png": ".png", "bmp": ".bmp", "gif": ".gif"}


def cover_extension(m4b):
    """Return the file extension for the embedded cover, or None if there is none."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_name",
            "-of", "csv=p=0",
            str(m4b),
        ],
        capture_output=True,
        text=True,
        errors="replace",
    )
    codec = result.stdout.strip()
    return CODEC_EXT.get(codec)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "books_dir",
        nargs="?",
        default=str(DEFAULT_BOOKS_DIR),
        help="folder with .m4b files (default: ~/books)",
    )
    args = parser.parse_args()
    books_dir = Path(args.books_dir)

    m4b_files = sorted(books_dir.glob("*.m4b"))
    if not m4b_files:
        raise SystemExit(f"No .m4b files in {books_dir} — run decode.py first.")

    print(f"Found {len(m4b_files)} files.")
    for i, m4b in enumerate(m4b_files, 1):
        print(f"[{i}/{len(m4b_files)}] {m4b.name}")

        ext = cover_extension(m4b)
        if ext is None:
            print("  skipping (no embedded cover)")
            continue

        out = m4b.with_suffix(ext)
        if out.exists():
            print(f"  skipping (already exists): {out.name}")
            continue

        result = subprocess.run(
            [
                "ffmpeg",
                "-i", str(m4b),
                "-an",
                "-vcodec", "copy",
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
