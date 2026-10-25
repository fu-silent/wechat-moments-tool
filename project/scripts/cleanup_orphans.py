#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Remove orphaned files under <deliverable>/media and raw_xmls that are not referenced by the JSON.

Only touches files the current archive JSON does not reference (e.g. leftovers from an interrupted run).
Never deletes referenced media, so it is safe for the append-only archive.
"""
import argparse
import glob
import json
import os

ap = argparse.ArgumentParser()
ap.add_argument("--dir", required=True)
ap.add_argument("--json", default="")
ap.add_argument("--dry-run", action="store_true")
a = ap.parse_args()
DELIV = os.path.abspath(a.dir)
if a.json:
    jp = a.json
else:
    cands = sorted(glob.glob(os.path.join(DELIV, "moments_*.json")))
    if not cands:
        raise SystemExit("no moments_*.json in " + DELIV)
    jp = cands[0]
d = json.load(open(jp, encoding="utf-8"))
refs = set()
for p in d.get("posts") or []:
    if p.get("raw_xml_file"):
        refs.add(p["raw_xml_file"].replace(os.sep, "/"))
for rec in (d.get("media") or []) + (d.get("comment_media") or []):
    for k in ("file", "thumb_file"):
        if rec.get(k):
            refs.add(rec[k].replace(os.sep, "/"))
removed, kept = 0, 0
for sub in ("media", "raw_xmls"):
    base = os.path.join(DELIV, sub)
    if not os.path.isdir(base):
        continue
    for root, dirs, files in os.walk(base):
        for fn in files:
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, DELIV).replace(os.sep, "/")
            if rel in refs:
                kept += 1
                continue
            print(("would remove " if a.dry_run else "remove ") + rel)
            if not a.dry_run:
                os.remove(full)
            removed += 1
if not a.dry_run:
    for sub in ("media", "raw_xmls"):
        base = os.path.join(DELIV, sub)
        if not os.path.isdir(base):
            continue
        for root, dirs, files in os.walk(base, topdown=False):
            if not os.listdir(root):
                os.rmdir(root)
print("kept=%d removed=%d%s" % (kept, removed, " (dry-run)" if a.dry_run else ""))
