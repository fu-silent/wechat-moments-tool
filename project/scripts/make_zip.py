#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Package a deliverable folder into a UTF-8 zip (media stored, text deflated)."""
import argparse
import os
import sys
import time
import zipfile

STORE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov", ".gif", ".zip"}
SKIP_DIRS = {"__pycache__", ".git"}
SKIP_FILES = {".DS_Store"}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    src = os.path.abspath(a.dir)
    out = os.path.abspath(a.out)
    if not os.path.isdir(src):
        sys.exit("not a directory: " + src)
    tmp = out + ".tmp"
    n = 0
    t0 = time.time()
    with zipfile.ZipFile(tmp, "w", allowZip64=True) as z:
        for root, dirs, files in os.walk(src):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for fn in sorted(files):
                if fn in SKIP_FILES:
                    continue
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, src).replace(os.sep, "/")
                ext = os.path.splitext(fn)[1].lower()
                comp = zipfile.ZIP_STORED if ext in STORE_EXT else zipfile.ZIP_DEFLATED
                zi = zipfile.ZipInfo(rel)
                zi.compress_type = comp
                zi.flag_bits |= 0x800  # UTF-8 filename flag
                st = os.stat(full)
                zi.date_time = time.localtime(st.st_mtime)[:6]
                with open(full, "rb") as f:
                    z.writestr(zi, f.read())
                n += 1
    os.replace(tmp, out)
    with zipfile.ZipFile(out) as z:
        bad = z.testzip()
    print("zip: %s  files=%d  size=%.1f MB  testzip=%s  %.1fs" % (
        out, n, os.path.getsize(out) / 1048576.0, bad or "OK", time.time() - t0))

if __name__ == "__main__":
    main()
