# audible_dl

Laddar ner ditt eget Audible-bibliotek, en bok i taget med en slumpad paus emellan.

## Använd

```bash
pip install -r requirements.txt
python auth.py       # logga in en gång via webbläsaren, skapar auth.json
python download.py   # laddar ner allt till ~/books
python decode.py     # avkodar .aax → .m4b (DRM-fritt)
```

## Noter

- `auth.json` innehåller din session — dela inte, checka inte in i git.
- Inloggningen sker i din vanliga webbläsare (`from_login_external`). Landar du
  på en "Page not found"-sida är det rätt — kopiera den URL:en tillbaka in i
  terminalen. På macOS importeras `readline` så långa URL:er inte klipps av.
- Filerna är AAX (DRM). Avkodningen använder kontots **activation_bytes**
  (hämtas automatiskt av `decode.py` via `auth.json`) tillsammans med ffmpeg.
- Nedladdningen kräver en app-lik `User-Agent`, annars svarar CloudFront `403`.
- Pausen (`MIN_DELAY`/`MAX_DELAY` i `download.py`) är till för att inte belasta
  servern i onödan.

## Krav

- Python 3.10–3.12 (`audible`-biblioteket stödjer inte 3.13+).
- `ffmpeg` i PATH (för `decode.py`).
