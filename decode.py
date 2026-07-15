"""Decodes downloaded Audible files into DRM-free .m4b using ffmpeg.

Handles both DRM formats, detected per file from the ftyp brand (not the
filename — a download named .aax can still be AAXC inside):
  * AAX  — decrypted with the account-wide activation_bytes.
  * AAXC — decrypted with the per-file key/iv from the book's .voucher.

The .m4b keeps the audio, chapters and cover art (embedded as attached_pic),
and is written faststart so players can open it immediately.

Run download.py first. Requires ffmpeg in PATH.

Usage: python decode.py [BOOKS_DIR]   (BOOKS_DIR defaults to ~/books)
"""
import argparse
import re
import subprocess
import tempfile
from pathlib import Path

import audible
from audible.aescipher import _decrypt_voucher

AUTH_FILE = "auth.json"
DEFAULT_BOOKS_DIR = Path.home() / "books"


def major_brand(path):
    """ftyp major brand of an MP4 file, e.g. 'aax' or 'aaxc'."""
    with open(path, "rb") as f:
        header = f.read(12)
    return header[8:12].decode("ascii", "replace").strip()


def voucher_key_iv(book, auth):
    """Decrypt <book>.voucher into (key, iv) hex strings for an AAXC file."""
    voucher_file = book.with_suffix(".voucher")
    if not voucher_file.exists():
        raise FileNotFoundError(f"missing voucher: {voucher_file.name}")
    m = re.search(r"\[([A-Z0-9]+)\]", book.stem)
    if not m:
        raise ValueError(f"no ASIN in filename: {book.name}")
    di, ci = auth.device_info, auth.customer_info
    voucher = _decrypt_voucher(
        device_serial_number=di["device_serial_number"],
        customer_id=ci["user_id"],
        device_type=di["device_type"],
        asin=m.group(1),
        voucher=voucher_file.read_text().strip(),
    )
    return voucher["key"], voucher["iv"]


def has_cover(book):
    """True if the file carries a video (cover art) stream."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v",
         "-show_entries", "stream=index", "-of", "csv=p=0", str(book)],
        capture_output=True, text=True,
    )
    return bool(result.stdout.strip())


def decode_book(book, out, decrypt):
    """Mux one book into DRM-free .m4b. `decrypt` is the ffmpeg input flags
    that unlock the source (activation_bytes for AAX, key/iv for AAXC)."""
    with tempfile.TemporaryDirectory() as tmp:
        cover = Path(tmp) / "cover.jpg"
        if has_cover(book):
            # Extract the cover to a clean JPEG first; copying the source
            # video track verbatim carries a broken codec tag that players
            # choke on, so we re-embed the extracted image as attached_pic.
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", *decrypt, "-i", str(book),
                 "-an", "-map", "0:v:0", "-c", "copy", "-frames:v", "1", str(cover)],
                capture_output=True, text=True, errors="replace",
            )

        cmd = ["ffmpeg", "-y", "-v", "error", *decrypt, "-i", str(book)]
        if cover.exists():
            cmd += ["-i", str(cover)]
        cmd += ["-map", "0:a:0", "-map_chapters", "0"]
        if cover.exists():
            cmd += ["-map", "1:v:0", "-disposition:v:0", "attached_pic"]
        cmd += ["-c", "copy", "-movflags", "+faststart", str(out)]

        return subprocess.run(cmd, capture_output=True, text=True, errors="replace")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "books_dir",
        nargs="?",
        default=str(DEFAULT_BOOKS_DIR),
        help="folder with .aax/.aaxc files (default: ~/books)",
    )
    args = parser.parse_args()
    books_dir = Path(args.books_dir)

    auth = audible.Authenticator.from_file(AUTH_FILE)

    # activation_bytes needs a network call, so fetch it only if an AAX file
    # actually turns up (an all-AAXC library never needs it).
    _cache = []
    def activation_bytes():
        if not _cache:
            _cache.append(auth.get_activation_bytes())
        return _cache[0]

    books = sorted([*books_dir.glob("*.aax"), *books_dir.glob("*.aaxc")])
    if not books:
        raise SystemExit(f"No .aax/.aaxc files in {books_dir} — run download.py first.")

    print(f"Found {len(books)} files to decode.")
    for i, book in enumerate(books, 1):
        out = book.with_suffix(".m4b")
        print(f"[{i}/{len(books)}] {book.name}")
        if out.exists():
            print(f"  skipping (already exists): {out.name}")
            continue

        try:
            if major_brand(book) == "aaxc":
                key, iv = voucher_key_iv(book, auth)
                decrypt = ["-audible_key", key, "-audible_iv", iv]
            else:
                decrypt = ["-activation_bytes", activation_bytes()]
            result = decode_book(book, out, decrypt)
        except Exception as e:
            print(f"  error: {e}")
            continue

        if result.returncode == 0:
            print(f"  done: {out.name}")
        else:
            print(f"  error: {result.stderr.strip().splitlines()[-1]}")
            out.unlink(missing_ok=True)  # remove partial file

    print("Done.")


if __name__ == "__main__":
    main()
