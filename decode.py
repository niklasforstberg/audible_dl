"""Decodes downloaded Audible files into DRM-free .m4b using ffmpeg.

Handles both DRM formats, detected per file from the ftyp brand (not the
filename — a download named .aax can still be AAXC inside):
  * AAX  — decrypted with the account-wide activation_bytes.
  * AAXC — decrypted with the per-file key/iv from the book's .voucher.

The .m4b keeps the audio, chapters and cover art (embedded as attached_pic),
and is written faststart so players can open it immediately.

Books are filed into an Audiobookshelf tree, either locally under BOOKS_DIR or —
with --ship-to — on another machine, one book at a time: decode, check the audio
is real, copy it over, delete the local copy. The decoding machine then never
needs room for more than a single book, which is the point when the downloads
and the shelf live on different disks.

Run download.py first. Requires ffmpeg in PATH.

Usage: python decode.py [BOOKS_DIR] [--ship-to USER@HOST:/PATH]
       (BOOKS_DIR defaults to ~/books)
"""
import argparse
import datetime
import re
import shlex
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

import audible
from audible.aescipher import _decrypt_voucher

from download import ABS_DIRNAME, abs_dir, find_voucher, load_manifest, save_manifest
from verify import decode_errors

AUTH_FILE = "auth.json"
DEFAULT_BOOKS_DIR = Path.home() / "books"

# Where finished books are shipped, as user@host:/path. With this set (or
# --ship-to given) a book is decoded locally, checked, copied to the shelf and
# the local copy deleted — so the decoding machine never holds the whole
# library, only one book at a time. Leave it None to keep everything local.
SHIP_TO = None  # e.g. "niklas@10.0.20.10:/mnt/books/library"

# How much audio to test-decode before a book is allowed onto the shelf. A book
# decoded with the wrong key looks perfect and sounds like noise; shipping it
# and deleting the local copy would hide that until someone tried to listen.
VERIFY_SECONDS = 30


def major_brand(path):
    """ftyp major brand of an MP4 file, e.g. 'aax' or 'aaxc'."""
    with open(path, "rb") as f:
        header = f.read(12)
    return header[8:12].decode("ascii", "replace").strip()


def asin_of(book):
    """The ASIN download.py wrote into the filename, e.g. 'B0ABC12345'."""
    m = re.search(r"\[([A-Z0-9]+)\]", book.stem)
    if not m:
        raise ValueError(f"no ASIN in filename: {book.name}")
    return m.group(1)


def shelf_path(book, manifest):
    """This book's place on the shelf, relative to the library root, or None if
    the manifest doesn't know enough about it yet to file it."""
    meta = manifest.get(asin_of(book)) or {}
    if not meta.get("title"):
        return None
    return abs_dir(asin_of(book), meta) / f"{book.stem}.m4b"


def output_path(books_dir, book, manifest):
    """Where this book's .m4b belongs in a local Audiobookshelf tree.

    The layout comes from the metadata download.py recorded in the manifest.
    A book with no metadata yet (manifest deleted, or downloaded before this
    existed) keeps the old flat name beside its source, so it still decodes."""
    rel = shelf_path(book, manifest)
    if rel is None:
        return book.with_suffix(".m4b")
    return books_dir / ABS_DIRNAME / rel


def split_destination(dest):
    """"user@host:/path" into its two halves."""
    host, sep, root = dest.partition(":")
    if not (sep and host and root.startswith("/")):
        raise SystemExit(f"--ship-to wants user@host:/absolute/path, got: {dest}")
    return host, root


def remote_shelf(host, root):
    """Every .m4b already on the shelf, as {relative path: size in bytes}.

    One listing for the whole library rather than a question per book: it is
    what lets a run repair itself — anything missing or the wrong size gets
    decoded again — without turning into hundreds of round trips. A shelf that
    doesn't exist yet is simply an empty one."""
    listing = f"find {shlex.quote(root)} -type f -name '*.m4b' -printf '%s %P\\n'"
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", host, listing],
        capture_output=True, text=True, errors="replace",
    )
    if result.returncode and "No such file" not in result.stderr:
        raise SystemExit(f"cannot read the shelf on {host}: {result.stderr.strip()}")

    shelf = {}
    for line in result.stdout.splitlines():
        size, _, rel = line.partition(" ")
        if rel:
            shelf[rel] = int(size)
    return shelf


def ship(local, host, root, rel):
    """Copy one finished book onto the shelf.

    rsync transfers to a temporary name and renames on success, so an
    interrupted copy can never leave something that looks like a whole book;
    it also checksums what it wrote. A failure raises, and the caller keeps
    the local file rather than deleting it."""
    target = PurePosixPath(root) / rel
    subprocess.run(
        ["ssh", "-o", "BatchMode=yes", host, f"mkdir -p {shlex.quote(str(target.parent))}"],
        check=True, capture_output=True, text=True,
    )
    subprocess.run(
        ["rsync", "-a", "-e", "ssh -o BatchMode=yes",
         str(local), f"{host}:{shlex.quote(str(target))}"],
        check=True, capture_output=True, text=True,
    )


def voucher_key_iv(book, auth):
    """Decrypt <book>.voucher into (key, iv) hex strings for an AAXC file."""
    voucher_file = find_voucher(book.parent, book.stem)
    if voucher_file is None:
        raise FileNotFoundError(f"missing voucher for: {book.name}")
    di, ci = auth.device_info, auth.customer_info
    voucher = _decrypt_voucher(
        device_serial_number=di["device_serial_number"],
        customer_id=ci["user_id"],
        device_type=di["device_type"],
        asin=asin_of(book),
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


def last_line(text):
    """The last non-empty line of a subprocess's complaint."""
    lines = [l for l in (text or "").strip().splitlines() if l.strip()]
    return lines[-1] if lines else "no output"


def decode_one(book, out, auth, activation_bytes):
    """Decode one book to `out`. Returns an error message, or None on success."""
    try:
        if major_brand(book) == "aaxc":
            key, iv = voucher_key_iv(book, auth)
            decrypt = ["-audible_key", key, "-audible_iv", iv]
        else:
            decrypt = ["-activation_bytes", activation_bytes()]
        result = decode_book(book, out, decrypt)
    except Exception as e:
        return str(e)

    if result.returncode:
        out.unlink(missing_ok=True)  # remove partial file
        return last_line(result.stderr)
    return None


def note_decoded(books_dir, manifest, asin, rel, size):
    """Record that a book is on the shelf, and where."""
    manifest.setdefault(asin, {})["decoded"] = {
        "path": str(rel),
        "size": size,
        "at": datetime.date.today().isoformat(),
    }
    save_manifest(books_dir, manifest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "books_dir",
        nargs="?",
        default=str(DEFAULT_BOOKS_DIR),
        help="folder with .aax/.aaxc files (default: ~/books)",
    )
    parser.add_argument(
        "--ship-to",
        default=SHIP_TO,
        metavar="USER@HOST:/PATH",
        help="copy each finished book to this shelf and delete the local copy",
    )
    parser.add_argument(
        "--seconds",
        type=int,
        default=VERIFY_SECONDS,
        help=f"audio to test-decode before shipping (default: {VERIFY_SECONDS})",
    )
    args = parser.parse_args()
    books_dir = Path(args.books_dir)

    auth = audible.Authenticator.from_file(AUTH_FILE)
    manifest = load_manifest(books_dir)

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

    host = root = shelf = None
    if args.ship_to:
        host, root = split_destination(args.ship_to)
        shelf = remote_shelf(host, root)
        print(f"Shelf {host}:{root} holds {len(shelf)} books.")

    print(f"Found {len(books)} files to decode.")
    counts = {"ok": 0, "decoded": 0, "failed": 0}

    for i, book in enumerate(books, 1):
        print(f"[{i}/{len(books)}] {book.name}")
        try:
            asin, rel = asin_of(book), shelf_path(book, manifest)
        except ValueError as e:
            print(f"  error: {e}")
            counts["failed"] += 1
            continue

        if args.ship_to:
            if rel is None:
                print("  skipping: nothing in the manifest to file it under —"
                      " run download.py first")
                counts["failed"] += 1
                continue

            # The shelf itself is the authority on what is done, so a book
            # deleted over there comes back on the next run.
            on_shelf = shelf.get(str(rel))
            recorded = (manifest.get(asin) or {}).get("decoded") or {}
            if on_shelf is not None and recorded.get("size") in (None, on_shelf):
                print(f"  ok: on the shelf")
                if recorded.get("size") != on_shelf:
                    note_decoded(books_dir, manifest, asin, rel, on_shelf)
                counts["ok"] += 1
                continue
            if on_shelf is not None:
                print(f"  re-decoding: {on_shelf} bytes on the shelf,"
                      f" {recorded['size']} expected")

            # Decoded beside the downloads, never into a tree we are about to
            # delete — one book's worth of space at a time is the whole point.
            out = books_dir / f"{book.stem}.m4b.staging"
        else:
            out = output_path(books_dir, book, manifest)
            if out.exists():
                print(f"  skipping (already exists): {out.name}")
                counts["ok"] += 1
                continue

            out.parent.mkdir(parents=True, exist_ok=True)

            # Decoding is the expensive part, so a book decoded before the shelf
            # layout existed is moved into place rather than decoded again.
            flat = book.with_suffix(".m4b")
            if flat != out and flat.exists():
                flat.replace(out)
                print(f"  filed: {out.relative_to(books_dir)}")
                counts["ok"] += 1
                continue

        error = decode_one(book, out, auth, activation_bytes)
        if error:
            print(f"  error: {error}")
            counts["failed"] += 1
            continue

        if not args.ship_to:
            print(f"  done: {out.name}")
            counts["decoded"] += 1
            continue

        # Prove the audio is real while the source is still here to redo it
        # from. A wrong-key decode is silent — right size, right chapters,
        # noise — and shipping it would delete the only evidence.
        bad, first = decode_errors(out, args.seconds)
        if bad:
            print(f"  BROKEN, not shipped: {bad} decode errors, e.g. {first}")
            out.unlink(missing_ok=True)
            counts["failed"] += 1
            continue

        size = out.stat().st_size
        try:
            ship(out, host, root, rel)
        except subprocess.CalledProcessError as e:
            # The local copy stays, so the next run resumes rather than redoes.
            print(f"  error shipping: {last_line(e.stderr)}")
            counts["failed"] += 1
            continue

        out.unlink()
        note_decoded(books_dir, manifest, asin, rel, size)
        print(f"  shipped: {rel}")
        counts["decoded"] += 1

    print(f"\nOK: {counts['ok']}  Decoded: {counts['decoded']}  Failed: {counts['failed']}")


if __name__ == "__main__":
    main()
