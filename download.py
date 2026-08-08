"""Download the whole library, one book at a time with a randomized pause in between.

Re-running is cheap and repairs the folder: every finished download records its
byte size in library.json, so a later run re-checks each book against that
record locally and only re-downloads the ones that are missing, truncated or
have lost their voucher. Books already present from before the manifest existed
cost one size lookup against the server, once.

Run auth.py first to create auth.json.

Usage: python download.py [OUT_DIR]   (OUT_DIR defaults to ~/books)
"""
import argparse
import datetime
import json
import os
import random
import time
from pathlib import Path

import audible
import httpx

AUTH_FILE = "auth.json"
DEFAULT_OUT_DIR = Path.home() / "books"

# Vouchers live in their own subfolder to keep the book folder readable.
VOUCHER_DIRNAME = "vouchers"

# What we know about each finished download, keyed by ASIN. Lives with the books
# rather than in the repo, and is safe to delete — a missing manifest only costs
# one size lookup per book to rebuild.
MANIFEST_NAME = "library.json"

# The Audiobookshelf library root, inside the books folder. Only decoded .m4b
# files go here — ABS never needs to see the .aaxc downloads or the vouchers.
ABS_DIRNAME = "library"

# The two container formats Audible hands out.
EXTS = ("aax", "aaxc")

# Randomized pause between each download (seconds). Easy on the server. Only
# applies to books we actually fetch; locally verified ones cost nothing.
MIN_DELAY = 10
MAX_DELAY = 20

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
            response_groups="product_desc,product_attrs,contributors,series",
        )
        items = resp.get("items", [])
        if not items:
            break
        books.extend(items)
        page += 1
    return books


def safe_name(text):
    """A single path component, stripped of anything awkward in a filename.

    Deliberately conservative, and deliberately frozen: this decides the name of
    every .aax/.aaxc on disk, so loosening it would make already-downloaded books
    look missing and fetch the whole library again."""
    return "".join(c for c in text if c.isalnum() or c in " -_").strip()


# Characters that are unsafe in a path component, plus the ones Windows rejects
# — harmless to exclude here and it keeps the shelf portable.
UNSAFE_PATH_CHARS = set('/\\:*?"<>|')


def safe_component(text):
    """A folder name for the shelf. Unlike safe_name this keeps punctuation, so
    "Iain M. Banks" and "Destiny's Crucible" survive intact — Audiobookshelf
    matches books against Audible by author and title, and stripped names miss."""
    cleaned = "".join(" " if c in UNSAFE_PATH_CHARS or ord(c) < 32 else c for c in text)
    # A component that is all dots, or ends in one, confuses some filesystems.
    return " ".join(cleaned.split()).strip(". ")


def book_stem(item):
    """Filename for a library item, without extension. The single definition —
    check.py and decode.py derive their filenames from this one."""
    title = item.get("title", item["asin"])
    return f"{safe_name(title)} [{item['asin']}]"


def book_meta(item):
    """The parts of a library item that decide where its .m4b is filed.
    Recorded in the manifest so decode.py needs no network of its own."""
    authors = [a["name"] for a in (item.get("authors") or []) if a.get("name")]
    series = (item.get("series") or [None])[0]
    return {
        "title": item.get("title") or item["asin"],
        "authors": authors,
        "series": (series or {}).get("title"),
        "sequence": (series or {}).get("sequence"),
    }


def abs_dir(asin, meta):
    """Where a book's .m4b lives, relative to the Audiobookshelf library root.

    ABS reads {Author}/{Series}/{Book} or {Author}/{Book} from the folder names.
    A series sequence has to lead the book folder and be followed by " - " —
    it keeps its decimal, so a "Book 7.5" side story still sorts where it should.
    """
    author = safe_component(authors_label(meta)) or "Unknown Author"
    title = safe_component(meta.get("title") or "") or asin

    series = safe_component(meta.get("series") or "")
    if not series:
        return Path(author) / title

    seq = (meta.get("sequence") or "").strip()
    return Path(author) / series / (f"{seq} - {title}" if seq else title)


def authors_label(meta):
    """One author for the folder name. ABS treats the author folder as a single
    name, so co-authored books file under the first — the full list still rides
    along in the file's own tags."""
    authors = meta.get("authors") or []
    return authors[0] if authors else ""


def find_local(books_dir, stem):
    """The downloaded book file, whichever container it turned out to be."""
    for ext in EXTS:
        path = books_dir / f"{stem}.{ext}"
        if path.exists():
            return path
    return None


def find_part(books_dir, stem):
    """A leftover .part file from an interrupted download."""
    for ext in EXTS:
        path = books_dir / f"{stem}.{ext}.part"
        if path.exists():
            return path
    return None


def find_voucher(books_dir, stem):
    """The book's .voucher, from the vouchers/ subfolder or — for books
    downloaded before that folder existed — from beside the book itself."""
    for candidate in (
        books_dir / VOUCHER_DIRNAME / f"{stem}.voucher",
        books_dir / f"{stem}.voucher",
    ):
        if candidate.exists():
            return candidate
    return None


def load_manifest(books_dir):
    path = books_dir / MANIFEST_NAME
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        # A corrupt manifest costs a re-check, not the library. Don't die on it.
        print(f"  warning: ignoring unreadable {MANIFEST_NAME}: {e}")
        return {}


def save_manifest(books_dir, manifest):
    """Write via a temp file so an interrupted run can't truncate the manifest."""
    path = books_dir / MANIFEST_NAME
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    os.replace(tmp, path)


def request_license(client, asin):
    """Ask for a download license — the URL plus, for AAXC, the voucher."""
    return client.post(
        f"content/{asin}/licenserequest",
        body={
            "consumption_type": "Download",
            "drm_type": "Adrm",
            "quality": QUALITY,
            "response_groups": "last_position_heard,content_reference,chapter_info",
        },
    )


class LicenseDenied(RuntimeError):
    """The server declined to license this book for download. Distinct from a
    network failure: it is an answer, not an outage, and repeating the request
    gets the same answer."""


def license_url(dl):
    """The download URL out of a license response.

    A refusal comes back as a normal response with no content_url in it, so
    reaching straight for the URL turns an explained "Denied" into a bare
    KeyError. Some titles in a library — Audible Originals in particular — are
    listenable but were never licensed for download; the server's own wording
    is the useful thing to report."""
    lic = dl.get("content_license") or {}
    url = ((lic.get("content_metadata") or {}).get("content_url") or {}).get("offline_url")
    if url:
        return url

    status = lic.get("status_code") or "no download URL"
    message = lic.get("message") or "license response carried no content_url"
    if lic.get("status_code") == "Denied":
        raise LicenseDenied(f"{status}: {message}")
    raise RuntimeError(f"{status}: {message}")


def expected_size(client, asin):
    """The download's total byte size, without downloading its body."""
    url = license_url(request_license(client, asin))

    # Ranged GET: read only the size header, then close without reading the body.
    headers = {**DOWNLOAD_UA, "Range": "bytes=0-0"}
    with httpx.stream("GET", url, follow_redirects=True, timeout=60, headers=headers) as r:
        r.raise_for_status()
        content_range = r.headers.get("Content-Range")  # "bytes 0-0/12345"
        if content_range and "/" in content_range:
            return int(content_range.rsplit("/", 1)[-1])
        return int(r.headers.get("Content-Length", 0)) or None


def local_verdict(out_dir, item, manifest, retry_denied=False):
    """Judge a book from local files alone — no network. Returns (verdict, detail):
      "ok"         nothing to do
      "fetch"      must be downloaded, detail says why
      "unverified" present, but we never recorded its size; needs a server check
      "denied"     refused a licence before, and nothing on disk to fall back on
      "settled"    present, but its size can never be checked — see below
    """
    stem = book_stem(item)

    # A refusal is remembered so a scheduled run doesn't spend a request and a
    # pause re-asking a question that was already answered. --retry-denied asks
    # again, for when the licence situation may genuinely have changed.
    record = manifest.get(item["asin"]) or {}
    denied = record.get("denied") and not retry_denied

    local = find_local(out_dir, stem)

    if local is None:
        if denied:
            return "denied", record["denied"]
        if find_part(out_dir, stem):
            return "fetch", "incomplete (.part only)"
        return "fetch", "missing"

    if record.get("size") is None:
        # A refused licence blocks the size lookup too, since that also needs a
        # licence. The book is downloaded and decodable, its size is simply not
        # knowable — so take it as it is rather than asking again every run.
        if denied:
            return "settled", local
        return "unverified", local
    if record.get("quality") != QUALITY:
        return "fetch", f"downloaded at quality {record.get('quality')}, want {QUALITY}"

    actual = local.stat().st_size
    if actual != record["size"]:
        return "fetch", f"wrong size: {actual} on disk, {record['size']} expected"

    # A book whose voucher went missing can never be decoded, and nothing else
    # would ever notice — the book file itself looks perfectly complete.
    if record.get("voucher") and find_voucher(out_dir, stem) is None:
        return "fetch", "voucher missing"

    return "ok", local


def download_book(client, item, out_dir):
    """Download one book, replacing whatever is already there. Returns the
    manifest record describing the finished file."""
    stem = book_stem(item)

    dl = request_license(client, item["asin"])
    url = license_url(dl)
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

    # Save the voucher (the AAXC decryption keys) *before* the rename: the final
    # name is what marks a book as complete, and a book that exists without its
    # voucher can never be decoded and is never retried.
    voucher = dl["content_license"].get("license_response")
    if voucher:
        voucher_dir = out_dir / VOUCHER_DIRNAME
        voucher_dir.mkdir(parents=True, exist_ok=True)
        (voucher_dir / f"{stem}.voucher").write_text(str(voucher))

    # Replaces the old file if we are re-downloading a damaged one.
    part_path.replace(out_path)

    print(f"  done: {out_path.name}")
    return {
        "file": out_path.name,
        "size": size,
        "voucher": bool(voucher),
        "quality": QUALITY,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "out_dir",
        nargs="?",
        default=str(DEFAULT_OUT_DIR),
        help="target folder (default: ~/books)",
    )
    parser.add_argument(
        "--retry-denied",
        action="store_true",
        help="ask again about books the server previously refused to license",
    )
    args = parser.parse_args()
    out_dir = Path(args.out_dir)

    if not os.path.exists(AUTH_FILE):
        raise SystemExit("No auth.json — run auth.py first.")

    out_dir.mkdir(parents=True, exist_ok=True)
    auth = audible.Authenticator.from_file(AUTH_FILE)
    manifest = load_manifest(out_dir)
    counts = {"ok": 0, "downloaded": 0, "denied": 0, "failed": 0}
    failed = []

    with audible.Client(auth=auth) as client:
        library = get_library(client)
        print(f"Found {len(library)} books in the library.")

        for i, item in enumerate(library, 1):
            asin = item["asin"]
            title = item.get("title", asin)
            print(f"[{i}/{len(library)}] {title}")

            # The listing is already in hand, so recording where this book should
            # be filed costs nothing and needs no download — books that are
            # already complete pick up their metadata here too.
            manifest.setdefault(asin, {}).update(book_meta(item))

            verdict, detail = local_verdict(out_dir, item, manifest, args.retry_denied)

            if verdict == "ok":
                print(f"  ok: {detail.name}")
                counts["ok"] += 1
                continue  # no server call, so no pause either

            if verdict == "settled":
                print(f"  ok: {detail.name} (size unverifiable — licence refused)")
                counts["ok"] += 1
                continue  # the file is here; asking again would only be refused

            if verdict == "denied":
                print(f"  skipping (refused before): {detail}")
                counts["denied"] += 1
                continue  # settled by the manifest, so no server call and no pause

            try:
                # Any attempt that gets this far supersedes a remembered refusal;
                # if the answer is still no, it gets written down again below.
                manifest[asin].pop("denied", None)
                manifest[asin].pop("denied_at", None)

                if verdict == "unverified":
                    # Downloaded before the manifest existed: ask once, record it,
                    # and only re-download if the size actually disagrees.
                    local = detail
                    size = expected_size(client, asin)
                    if size is not None and local.stat().st_size == size:
                        manifest[asin].update({
                            "file": local.name,
                            "size": size,
                            "voucher": find_voucher(out_dir, book_stem(item)) is not None,
                            "quality": QUALITY,
                        })
                        save_manifest(out_dir, manifest)
                        print(f"  ok: {local.name} (verified against server)")
                        counts["ok"] += 1
                    else:
                        print(f"  re-downloading: size {local.stat().st_size} on disk, {size} on server")
                        manifest[asin].update(download_book(client, item, out_dir))
                        save_manifest(out_dir, manifest)
                        counts["downloaded"] += 1
                else:
                    print(f"  downloading: {detail}")
                    manifest[asin].update(download_book(client, item, out_dir))
                    save_manifest(out_dir, manifest)
                    counts["downloaded"] += 1
            except LicenseDenied as e:
                # An answer, not an outage — remember it so later runs cost nothing.
                print(f"  denied: {e}")
                manifest[asin].update({
                    "denied": str(e),
                    "denied_at": datetime.date.today().isoformat(),
                })
                save_manifest(out_dir, manifest)
                counts["denied"] += 1
            except Exception as e:
                print(f"  error: {e}")
                counts["failed"] += 1
                failed.append(f"{title} [{asin}]: {e}")

            # Everything still running here touched the server; books settled
            # locally took the `continue` above and cost nothing.
            if i < len(library):
                delay = random.uniform(MIN_DELAY, MAX_DELAY)
                print(f"  waiting {delay:.0f}s ...")
                time.sleep(delay)

    # Books that needed no download never hit a save above, so persist the
    # metadata picked up for them in one go.
    save_manifest(out_dir, manifest)

    print(
        f"\nOK: {counts['ok']}  Downloaded: {counts['downloaded']}  "
        f"Denied: {counts['denied']}  Failed: {counts['failed']}"
    )
    if counts["denied"]:
        print(
            f"{counts['denied']} book(s) the server won't license for download. "
            "They cost nothing on later runs; use --retry-denied to ask again."
        )
    if failed:
        # Errors scroll away over a run this long, so repeat them at the end.
        print("\nFailed books (re-run to retry them):")
        for line in failed:
            print(f"  {line}")


if __name__ == "__main__":
    main()
