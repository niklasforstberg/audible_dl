# audible_dl

Downloads your own Audible library, one book at a time with a randomized pause in between.

## Usage

```bash
pip install -r requirements.txt
python auth.py       # log in once via the browser, creates auth.json
python download.py   # downloads everything to ~/books
# or: python download.py /mnt/storage/books   # to a custom folder
python decode.py     # decodes .aax → .m4b (DRM-free)
# or: python decode.py /mnt/storage/books   # from a custom folder
python check.py      # verify downloaded books match the server's size
```

`check.py` also takes an optional folder argument (`python check.py /mnt/storage/books`).

## Notes

- `auth.json` contains your session — don't share it, don't commit it to git.
- Login happens in your regular browser (`from_login_external`). Landing on a
  "Page not found" page is expected — copy that URL back into the terminal.
  On macOS, `readline` is imported so long URLs don't get truncated.
- Files are AAX (DRM). Decoding uses the account's **activation_bytes**
  (fetched automatically by `decode.py` via `auth.json`) together with ffmpeg.
- The download requires an app-like `User-Agent`, otherwise CloudFront responds `403`.
- The pause (`MIN_DELAY`/`MAX_DELAY` in `download.py`) exists to avoid
  unnecessarily loading the server.

## Requirements

- Python 3.10–3.12 (the `audible` library doesn't support 3.13+).
- `ffmpeg` in PATH (for `decode.py`).
