"""Avkodar nedladdade .aax-filer till DRM-fria .m4b med ffmpeg.

Kör download.py först. Kräver ffmpeg i PATH.
"""
import subprocess
from pathlib import Path

import audible

AUTH_FILE = "auth.json"
BOOKS_DIR = Path.home() / "books"


def main():
    auth = audible.Authenticator.from_file(AUTH_FILE)
    activation_bytes = auth.get_activation_bytes()

    aax_files = sorted(BOOKS_DIR.glob("*.aax"))
    if not aax_files:
        raise SystemExit(f"Inga .aax-filer i {BOOKS_DIR} — kör download.py först.")

    print(f"Hittade {len(aax_files)} filer att avkoda.")
    for i, aax in enumerate(aax_files, 1):
        out = aax.with_suffix(".m4b")
        print(f"[{i}/{len(aax_files)}] {aax.name}")
        if out.exists():
            print(f"  hoppar över (finns redan): {out.name}")
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
        )
        if result.returncode == 0:
            print(f"  klar: {out.name}")
        else:
            print(f"  fel: {result.stderr.strip().splitlines()[-1]}")
            out.unlink(missing_ok=True)  # ta bort halvfärdig fil

    print("Klart.")


if __name__ == "__main__":
    main()
