#!/usr/bin/env python3
"""Download the source corpora this app is built on.

Everything lands in data/raw/ and is never modified in place; build_db.py
treats it as read-only input.

Sources
-------
CCAT/CATSS morphologically analysed LXX (Rahlfs 1935 base text)
    http://ccat.sas.upenn.edu/gopher/text/religion/biblical/lxxmorph/
    Non-commercial study/teaching use. See data/raw/lxxmorph/0-readme.txt and
    0-user-declaration.txt, both downloaded alongside the text.

Dodson Greek Lexicon (public domain, concise glosses)
    https://github.com/biblicalhumanities/Dodson-Greek-Lexicon

Perseus LSJ (Liddell-Scott-Jones, CC BY-SA) -- fallback glosses for the many
    LXX lemmas that never occur in the New Testament and so are absent from
    Dodson.

Brenton English Septuagint (public domain, 1851)
    https://ebible.org/find/details.php?id=eng-Brenton&all=1
    Verse-per-line text used as the whole-verse English under the interlinear.
"""

from __future__ import annotations

import io
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
MORPH_DIR = RAW / "lxxmorph"
LEX_DIR = RAW / "lexica"
BRENTON_DIR = RAW / "brenton"

BRENTON = (
    "eng-Brenton_vpl.txt",
    "https://eBible.org/Scriptures/eng-Brenton_vpl.zip",
    "eng-Brenton_vpl.txt",
)

CCAT_BASE = "http://ccat.sas.upenn.edu/gopher/text/religion/biblical/lxxmorph/"

# Licence/provenance files kept next to the text so the terms travel with it.
CCAT_DOCS = ["0-readme.txt", "0-user-declaration.txt", "0-betacode.txt"]

_PERSEUS = (
    "https://raw.githubusercontent.com/PerseusDL/lexica/master/"
    "CTS_XML_TEI/perseus/pdllex/grc/lsj/grc.lsj.perseus-eng{n}.xml"
)

# Perseus splits LSJ across 27 volumes, roughly one per Greek letter. Fetching
# only the first gets you alpha and nothing else -- all 27 are required for
# usable coverage of LXX vocabulary.
LEXICA = [
    (
        "dodson.csv",
        "https://raw.githubusercontent.com/biblicalhumanities/"
        "Dodson-Greek-Lexicon/master/dodson.csv",
    ),
] + [(f"lsj-{n:02d}.xml", _PERSEUS.format(n=n)) for n in range(1, 28)]


def read_all(resp, budget: float) -> bytes:
    """Read a whole response body, bounding the total time it may take.

    The `timeout` argument to urlopen is the SOCKET timeout: it bounds how
    long one read waits for the next byte, not how long the transfer runs. A
    server that trickles a byte every few seconds satisfies it forever, and
    nothing else in this path sets a limit -- so a stalled download would
    hang the container build, where this script fetches ~290 MB over plain
    HTTP. Reading in chunks against a deadline gives the bound the socket
    timeout only appears to give.
    """
    deadline = time.monotonic() + budget
    chunks = []
    while True:
        # read1, not read: read(n) blocks until it has n bytes or the stream
        # ends, so against a slow trickle the deadline below would not be
        # reached for hours. read1 returns whatever one underlying read
        # yielded, which is what makes the check fire on time.
        chunk = resp.read1(65536)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        if time.monotonic() > deadline:
            raise TimeoutError(f"exceeded {budget:.0f}s total download budget")


def fetch_zip_member(url: str, dest: Path, member: str, retries: int = 3) -> bool:
    """Download a zip and keep one member. Skip when dest is already present."""
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  have  {dest.name} ({dest.stat().st_size:,} B)")
        return True
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "lxx-trainer/1.0 (personal study)"}
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = read_all(resp, budget=600)
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                dest.write_bytes(zf.read(member))
            print(f"  got   {dest.name} ({dest.stat().st_size:,} B)")
            return True
        except (KeyError, urllib.error.URLError, TimeoutError, zipfile.BadZipFile) as exc:
            if attempt == retries:
                print(f"  FAIL  {dest.name}: {exc}", file=sys.stderr)
                return False
            time.sleep(2 * attempt)
    return False


def fetch(url: str, dest: Path, retries: int = 3) -> bool:
    """Download url to dest unless already present. Returns True on success."""
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  have  {dest.name} ({dest.stat().st_size:,} B)")
        return True
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "lxx-trainer/1.0 (personal study)"}
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                # The largest of these is an LSJ volume of a few tens of MB;
                # ten minutes is far beyond any healthy transfer and still
                # ends a stall long before a build times out.
                data = read_all(resp, budget=600)
            dest.write_bytes(data)
            print(f"  got   {dest.name} ({len(data):,} B)")
            return True
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == retries:
                print(f"  FAIL  {dest.name}: {exc}", file=sys.stderr)
                return False
            time.sleep(2 * attempt)
    return False


def list_ccat_files() -> list[str]:
    """Read the CCAT directory index rather than hardcoding book filenames.

    The set is not guessable: several books ship split across two files
    (Isaiah1/Isaiah2, Jer1/Jer2, Ezek1/Ezek2) and several exist in two textual
    traditions (JoshA/JoshB, DanielOG/DanielTh, ...).
    """
    req = urllib.request.Request(
        CCAT_BASE, headers={"User-Agent": "lxx-trainer/1.0 (personal study)"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        # An index page, so a minute is generous; see read_all for why the
        # socket timeout above does not cover a stalled transfer.
        html = read_all(resp, budget=60).decode("utf-8", "replace")
    names = re.findall(r'href="([^"]+\.mlxx)"', html)
    # Preserve index order, drop any duplicate hrefs.
    seen, ordered = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            ordered.append(n)
    return ordered


def main() -> int:
    MORPH_DIR.mkdir(parents=True, exist_ok=True)
    LEX_DIR.mkdir(parents=True, exist_ok=True)
    BRENTON_DIR.mkdir(parents=True, exist_ok=True)

    print("CCAT/CATSS analysed Septuagint:")
    ccat_files = list_ccat_files()
    print(f"  index lists {len(ccat_files)} book files")
    failed = []
    for name in CCAT_DOCS + ccat_files:
        if not fetch(CCAT_BASE + name, MORPH_DIR / name):
            failed.append(name)

    print("\nLexica:")
    for name, url in LEXICA:
        if not fetch(url, LEX_DIR / name):
            failed.append(name)

    print("\nBrenton English Septuagint:")
    name, url, member = BRENTON
    if not fetch_zip_member(url, BRENTON_DIR / name, member):
        failed.append(name)

    if failed:
        print(f"\n{len(failed)} file(s) failed: {', '.join(failed)}", file=sys.stderr)
        return 1
    print("\nAll sources present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
