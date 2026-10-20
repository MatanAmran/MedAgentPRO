"""
MedAgentPro - the BIRD database for the generality check (task 11b, chat 20.10), outside the repository.

    python -m tools.bird_fetch               check what is there; download nothing
    python -m tools.bird_fetch --download    download dev.zip (346 MB) if absent, then check and extract

BIRD dev (bird-bench.github.io, CC BY-SA 4.0) is one zip of every dev database. Only superhero is extracted,
to BIRD_DIR/superhero.sqlite. Both the zip and the database are checked against the SHA-256 recorded here, so
a changed file is refused, never used. Nothing goes into git: BIRD_DIR defaults to ~/medagentpro-data/bird,
or the environment's MEDAGENTPRO_BIRD_DIR. The download runs only with --download (Evyatar approved it,
20.10, chat).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import urllib.request
import zipfile
from io import BytesIO
from pathlib import Path

URL = "https://bird-bench.oss-cn-beijing.aliyuncs.com/dev.zip"
DEV_ZIP_SIZE = 346_207_293
DEV_ZIP_SHA256 = "cdd6d19faeb45a23970b98d3ef6c40a87987c95459c2cf12076897a60cf5a630"   # Last-Modified 2024-06-29
SUPERHERO_SHA256 = "75e94a2c3236ee3bb2c01fb97a1c4b4c1c269bcefd4eab1d04be323d2d0825b1"
INNER_ZIP = "dev_20240627/dev_databases.zip"
SUPERHERO = "dev_databases/superhero/superhero.sqlite"


def bird_dir() -> Path:
    return Path(os.environ.get("MEDAGENTPRO_BIRD_DIR") or Path.home() / "medagentpro-data" / "bird")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def superhero_db() -> Path:
    """The checked database, or FileNotFoundError saying how to get it."""
    db = bird_dir() / "superhero.sqlite"
    if not db.exists():
        raise FileNotFoundError(f"{db} is missing: python -m tools.bird_fetch --download")
    if sha256(db) != SUPERHERO_SHA256:
        raise ValueError(f"{db} is not the recorded superhero database (SHA-256 differs)")
    return db


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.bird_fetch")
    ap.add_argument("--download", action="store_true", help=f"download dev.zip ({DEV_ZIP_SIZE / 1e6:.0f} MB) if absent")
    args = ap.parse_args(argv)
    home = bird_dir()
    home.mkdir(parents=True, exist_ok=True)
    zip_path = home / "dev.zip"
    if not zip_path.exists():
        if not args.download:
            print(f"{zip_path} is missing. To download {DEV_ZIP_SIZE / 1e6:.0f} MB from {URL}: --download")
            return 2
        print(f"downloading {URL} to {zip_path}")
        # a fixed https URL; the file is checked against its SHA-256 below
        urllib.request.urlretrieve(URL, zip_path)  # nosec B310
    if sha256(zip_path) != DEV_ZIP_SHA256:
        print(f"{zip_path}: SHA-256 differs from the recorded one; refused")
        return 1
    db = home / "superhero.sqlite"
    if not db.exists() or sha256(db) != SUPERHERO_SHA256:
        with zipfile.ZipFile(zip_path) as outer, zipfile.ZipFile(BytesIO(outer.read(INNER_ZIP))) as inner:
            db.write_bytes(inner.read(SUPERHERO))
    if sha256(db) != SUPERHERO_SHA256:
        print(f"{db}: SHA-256 differs from the recorded one; refused")
        return 1
    print(f"ok: {db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
