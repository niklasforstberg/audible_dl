"""Ladda ner hela biblioteket, en bok i taget med en slumpad paus emellan.

Kör auth.py först för att skapa auth.json.
"""
import os
import random
import time
from pathlib import Path

import audible
import httpx

AUTH_FILE = "auth.json"
OUT_DIR = Path.home() / "books"

# Slumpad paus mellan varje bok (sekunder). Skonsamt mot servern.
MIN_DELAY = 45
MAX_DELAY = 180

# Ljudkvalitet: "High", "Normal" eller "Extreme".
QUALITY = "High"

# CloudFront blockar okända User-Agents; app-lik UA krävs för nedladdningen.
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

    # Hämta nedladdningslänk för AAXC-filen.
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
    ext = url.split("?")[0].rsplit(".", 1)[-1].lower()  # aax eller aaxc

    out_path = OUT_DIR / f"{safe_title} [{asin}].{ext}"
    if out_path.exists():
        print(f"  hoppar över (finns redan): {out_path.name}")
        return

    # Spara även voucher (nycklar) för senare avkodning, om den finns (gäller AAXC).
    voucher = dl["content_license"].get("license_response")
    if voucher:
        (OUT_DIR / f"{safe_title} [{asin}].voucher").write_text(str(voucher))

    with httpx.stream("GET", url, follow_redirects=True, timeout=None, headers=DOWNLOAD_UA) as r:
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_bytes(chunk_size=1 << 16):
                f.write(chunk)
    print(f"  klar: {out_path.name}")


def main():
    if not os.path.exists(AUTH_FILE):
        raise SystemExit("Ingen auth.json — kör auth.py först.")

    OUT_DIR.mkdir(exist_ok=True)
    auth = audible.Authenticator.from_file(AUTH_FILE)

    with audible.Client(auth=auth) as client:
        library = get_library(client)
        print(f"Hittade {len(library)} böcker i biblioteket.")

        for i, item in enumerate(library, 1):
            title = item.get("title", item["asin"])
            print(f"[{i}/{len(library)}] {title}")
            try:
                download_book(client, item)
            except Exception as e:
                print(f"  fel: {e}")

            if i < len(library):
                delay = random.uniform(MIN_DELAY, MAX_DELAY)
                print(f"  väntar {delay:.0f}s ...")
                time.sleep(delay)

    print("Klart.")


if __name__ == "__main__":
    main()
