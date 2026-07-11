# audible_dl

Laddar ner ditt eget Audible-bibliotek, en bok i taget med en slumpad paus emellan.

## Använd

```bash
pip install -r requirements.txt
python auth.py       # logga in en gång, skapar auth.json
python download.py   # laddar ner allt till downloads/
```

## Noter

- `auth.json` innehåller din session — dela inte, checka inte in i git.
- Filerna är AAXC (DRM). En `.voucher` med nycklar sparas bredvid varje fil
  för avkodning i efterhand (t.ex. med ffmpeg + nyckeln ur vouchern).
- Pausen (`MIN_DELAY`/`MAX_DELAY` i `download.py`) är till för att inte belasta
  servern i onödan.
