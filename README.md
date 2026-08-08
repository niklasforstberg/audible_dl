# audible_dl

Downloads your own Audible library, one book at a time with a randomized pause in between.

## Usage

```bash
pip install -r requirements.txt
python auth.py       # log in once via the browser, creates auth.json
python download.py   # downloads everything to ~/books
python decode.py     # decodes the downloads → .m4b (DRM-free)
python verify.py     # confirm the .m4b files actually play
python check.py      # compare downloads against the server's size
```

Every script takes an optional folder argument
(`python download.py /mnt/storage/books`) and defaults to `~/books`.

## Folder layout

```
books/
  A Book [B0ABC12345].aaxc     the download
  library.json                 what each finished download should look like
  vouchers/
    A Book [B0ABC12345].voucher
  library/                     the Audiobookshelf library root
    Iain M. Banks/
      Culture/
        8 - Matter/
          Matter [B0DEF67890].m4b
    A. Writer/
      Some Standalone/
        Some Standalone [B0ABC12345].m4b
```

The decoded `.m4b` files go under `library/` in the layout Audiobookshelf
expects — `{Author}/{Series}/{Book}`, or `{Author}/{Book}` for a standalone.
A series sequence leads the book folder and is followed by `" - "`, which is
what ABS parses; decimals survive, so a "Book 7.5" side story sorts correctly.
Point an Audiobookshelf library at `books/library` and it will never see the
downloads or the vouchers.

The author and series names come from `library.json`, which `download.py`
fills in from the Audible library listing on every run — including for books
that are already complete, so the metadata backfills without re-downloading
anything. A book whose metadata isn't recorded yet decodes to the old flat
name beside its source.

## Re-running download.py

`download.py` is safe and cheap to re-run — it repairs the folder rather than
starting over. Each finished download records its byte size in `library.json`,
so a later run decides locally, without touching the network, whether a book is
sound. Only books that are missing, still `.part`, the wrong size, or missing
their voucher get fetched again. A full re-check of a few hundred books takes
seconds.

Books downloaded before `library.json` existed cost one size lookup against the
server each, once; the answer is then recorded like any other.

## Books the server won't license

Some titles sit in a library, listenable, that Audible never licensed for
download — Audible Originals in particular. The licence request comes back
`Denied` with a reason, and it will come back `Denied` every time.

Those refusals are recorded in `library.json` (`denied`, `denied_at`), and
later runs skip the book without spending a request or a pause. That matters
for a scheduled run: a handful of permanently-refused books would otherwise
add minutes and an error to every single run.

To ask again — because a licence really can change:

```bash
python download.py --retry-denied
```

A successful download clears the record; a fresh refusal rewrites it.

## Notes

- `auth.json` contains your session — don't share it, don't commit it to git.
- Login happens in your regular browser (`from_login_external`). Landing on a
  "Page not found" page is expected — copy that URL back into the terminal.
  On macOS, `readline` is imported so long URLs don't get truncated.
- **The files are AAXC, not AAX**, whatever the extension says — the format is
  detected per file from the ftyp brand. AAXC is decrypted with the per-file
  key/iv from that book's `.voucher`; `activation_bytes` does not apply and
  will not work. Genuine AAX files still take the `activation_bytes` path.
- A voucher can only be decrypted with the `auth.json` of the **device that
  downloaded the book**. Decode on the machine that did the downloading, or
  bring that `auth.json` with you.
- A book decoded with the wrong key still looks perfect — right size, right
  duration, right chapters — but its audio is unplayable noise. `verify.py`
  test-decodes each `.m4b` and lists the bad ones; it needs only ffmpeg, no
  auth and no network.
- Audiobookshelf needs only the `.m4b` — chapters, cover and tags are embedded
  in it. Deleting the `.aaxc` originals to save space is possible but not free:
  `download.py` decides what to re-fetch from the presence and size of those
  files, so a deleted original looks like a missing book and gets downloaded
  again on the next run.
- The download requires an app-like `User-Agent`, otherwise CloudFront responds `403`.
- The pause (`MIN_DELAY`/`MAX_DELAY` in `download.py`) exists to avoid
  unnecessarily loading the server. It only applies to books that are actually
  fetched, never to ones settled locally.

## Requirements

- Python 3.10–3.12 (the `audible` library doesn't support 3.13+).
- `ffmpeg` in PATH (for `decode.py` and `verify.py`).
