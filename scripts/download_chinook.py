"""Download the Chinook sample database (a digital music store, 11 tables)."""

import sys
import urllib.request
from pathlib import Path

URL = "https://github.com/lerocha/chinook-database/releases/download/v1.4.5/Chinook_Sqlite.sqlite"
DEST = Path(__file__).resolve().parent.parent / "data" / "chinook.sqlite"

if __name__ == "__main__":
    DEST.parent.mkdir(exist_ok=True)
    if DEST.exists():
        print(f"Already present: {DEST}")
        sys.exit(0)
    print(f"Downloading {URL}")
    urllib.request.urlretrieve(URL, DEST)
    print(f"Saved to {DEST} ({DEST.stat().st_size // 1024} KB)")
