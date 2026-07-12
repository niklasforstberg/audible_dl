"""Download the whole library, one book at a time with a randomized pause in between.

Run auth.py first to create auth.json.

Usage: python download.py [OUT_DIR]   (OUT_DIR defaults to ~/books)
"""
import argparse
import os
import random
import time
from pathlib import Path

import audible
import httpx

AUTH_FILE = "auth.json"
DEFAULT_OUT_DIR = Path.home() / "books"

# Randomized pause between each download (seconds). Easy on the server.
MIN_DELAY = 45
MAX_DELAY = 180

# Audio quality: "High", "Normal" or "Extreme".
QUALITY = "High"

# CloudFront blocks unknown User-Agents; an app-like UA is required for the download.
DOWNLOAD_UA = {"User-Agent": "Audible, iPhone, 4.0.1 (573), iPhone 12 Pro, iOS 15.0.2"}


def get_library(client):
    books = []
    page = 1
    while True:
        resp = client.get(
            "1.0/library",
            num_results=1000,
            page=page,
            response_groups="product_desc,product_attrs",
        )
        items = resp.get("items", [])
        if not items:
            break
        books.extend(items)
        page += 1
    return books


def book_exists(out_dir, stem):
    # A file only gets its final name once it's fully downloaded (see the .part
    # rename below), so an existing book file is guaranteed complete.
    return any((out_dir / f"{stem}.{ext}").exists() for ext in ("aax", "aaxc"))


def download_book(client, item, out_dir):
    """Download one book. Returns True if it contacted the download server,
    False if the book already existed and was skipped."""
    asin = item["asin"]
    title = item.get("title", asin)
    safe_title = "".join(c for c in title if c.isalnum() or c in " -_").strip()
    stem = f"{safe_title} [{asin}]"

    if book_exists(out_dir, stem):
        print(f"  skipping (already exists): {stem}")
        return False

    # Get the download link for the AAXC file.
    dl = client.post(
        f"content/{asin}/licenserequest",
        body={
            "consumption_type": "Download",
            "drm_type": "Adrm",
            "quality": QUALITY,
            "response_groups": "last_position_heard,content_reference,chapter_info",
        },
    )
    content_metadata = dl["content_license"]["content_metadata"]
    url = content_metadata["content_url"]["offline_url"]
    ext = url.split("?")[0].rsplit(".", 1)[-1].lower()  # aax or aaxc

    out_path = out_dir / f"{stem}.{ext}"
    part_path = out_dir / f"{stem}.{ext}.part"

    # Download to a .part file and only rename to the final name once complete,
    # so an interrupted/truncated download never leaves a valid-looking book file.
    with httpx.stream("GET", url, follow_redirects=True, timeout=None, headers=DOWNLOAD_UA) as r:
        r.raise_for_status()
        expected = int(r.headers.get("Content-Length", 0))
        with open(part_path, "wb") as f:
            for chunk in r.iter_bytes(chunk_size=1 << 16):
                f.write(chunk)

    size = part_path.stat().st_size
    if expected and size != expected:
        part_path.unlink(missing_ok=True)
        raise RuntimeError(f"incomplete download: got {size} of {expected} bytes")
    part_path.rename(out_path)

    # Save the voucher (keys) for later decoding only now that the whole book is
    # down, if present (applies to AAXC).
    voucher = dl["content_license"].get("license_response")
    if voucher:
        (out_dir / f"{stem}.voucher").write_text(str(voucher))

    print(f"  done: {out_path.name}")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "out_dir",
        nargs="?",
        default=str(DEFAULT_OUT_DIR),
        help="target folder (default: ~/books)",
    )
    args = parser.parse_args()
    out_dir = Path(args.out_dir)

    if not os.path.exists(AUTH_FILE):
        raise SystemExit("No auth.json — run auth.py first.")

    out_dir.mkdir(parents=True, exist_ok=True)
    auth = audible.Authenticator.from_file(AUTH_FILE)

    with audible.Client(auth=auth) as client:
        library = get_library(client)
        print(f"Found {len(library)} books in the library.")

        for i, item in enumerate(library, 1):
            title = item.get("title", item["asin"])
            print(f"[{i}/{len(library)}] {title}")
            try:
                downloaded = download_book(client, item, out_dir)
            except Exception as e:
                print(f"  error: {e}")
                downloaded = True  # we hit the server; pause before the next one

            # Only pause after an actual download, not after a skipped book.
            if downloaded and i < len(library):
                delay = random.uniform(MIN_DELAY, MAX_DELAY)
                print(f"  waiting {delay:.0f}s ...")
                time.sleep(delay)

    print("Done.")


if __name__ == "__main__":
    main()
