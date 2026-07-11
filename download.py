"""Download the whole library, one book at a time with a randomized pause in between.

Run auth.py first to create auth.json.
"""
import os
import random
import time
from pathlib import Path

import audible
import httpx

AUTH_FILE = "auth.json"
OUT_DIR = Path.home() / "books"

# Randomized pause between each book (seconds). Easy on the server.
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


def download_book(client, item):
    asin = item["asin"]
    title = item.get("title", asin)
    safe_title = "".join(c for c in title if c.isalnum() or c in " -_").strip()

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

    out_path = OUT_DIR / f"{safe_title} [{asin}].{ext}"
    if out_path.exists():
        print(f"  skipping (already exists): {out_path.name}")
        return

    # Also save the voucher (keys) for later decoding, if present (applies to AAXC).
    voucher = dl["content_license"].get("license_response")
    if voucher:
        (OUT_DIR / f"{safe_title} [{asin}].voucher").write_text(str(voucher))

    with httpx.stream("GET", url, follow_redirects=True, timeout=None, headers=DOWNLOAD_UA) as r:
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_bytes(chunk_size=1 << 16):
                f.write(chunk)
    print(f"  done: {out_path.name}")


def main():
    if not os.path.exists(AUTH_FILE):
        raise SystemExit("No auth.json — run auth.py first.")

    OUT_DIR.mkdir(exist_ok=True)
    auth = audible.Authenticator.from_file(AUTH_FILE)

    with audible.Client(auth=auth) as client:
        library = get_library(client)
        print(f"Found {len(library)} books in the library.")

        for i, item in enumerate(library, 1):
            title = item.get("title", item["asin"])
            print(f"[{i}/{len(library)}] {title}")
            try:
                download_book(client, item)
            except Exception as e:
                print(f"  error: {e}")

            if i < len(library):
                delay = random.uniform(MIN_DELAY, MAX_DELAY)
                print(f"  waiting {delay:.0f}s ...")
                time.sleep(delay)

    print("Done.")


if __name__ == "__main__":
    main()
