#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Append-only snapshot of WeChat 4.x sns.db (+ WAL) -> decrypted plaintext SQLite.

Why: some friends' Moments are only visible for a limited time (3 days / 1 month /
half year). Re-reading the live database would silently lose the rows WeChat has
already purged. This script never modifies existing snapshots and always creates a
new timestamped directory, so old data remains available to the incremental merge.

Layout:
  project/data/snapshots/<snapshot_id>/raw/sns.db (-wal, -shm)
  project/data/snapshots/<snapshot_id>/sns_plain.db
  project/data/snapshots/<snapshot_id>/meta.json
  project/data/snapshots/index.json          # append-only index

Usage:
  python3 snapshot_db.py                     # snapshot decided source dir, decrypt, index
  python3 snapshot_db.py --label manual      # custom label suffix in meta
  python3 snapshot_db.py --list              # list snapshots + current live DB info
"""
import argparse, glob, hashlib, json, os, shutil, sqlite3, sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import decrypt_sns as dec
import moments_core as em
import wxconfig

ROOT = em.ROOT


def snapshots_dir():
    return wxconfig.paths()["snapshots_dir"]


def find_src_dir():
    """Locate the live WeChat sns dir (config wechat.sns_dir, else auto-detect)."""
    return wxconfig.resolve_sns_dir()


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def copy_raw(src, rawdir):
    os.makedirs(rawdir, exist_ok=True)
    files = []
    for name in ("sns.db", "sns.db-wal", "sns.db-shm"):
        s = os.path.join(src, name)
        if not os.path.exists(s):
            continue
        d = os.path.join(rawdir, name)
        shutil.copy2(s, d)
        files.append({"name": name, "size": os.path.getsize(d),
                      "mtime": datetime.fromtimestamp(os.path.getmtime(s)).isoformat(timespec="seconds"),
                      "sha256": sha256_file(d)})
    return files


def decrypt_to_plain(db_path, wal_path, out_path):
    p1 = open(db_path, "rb").read(dec.PAGE)
    salt = p1[:dec.SALT_SZ]
    enc_key = dec.derive_enc_key(wxconfig.master_key_bytes(), salt)
    if not dec.verify_page1(enc_key, p1):
        raise SystemExit("[ERROR] page1 HMAC mismatch for %s (salt %s)" % (db_path, salt.hex()))
    pages = dec.decrypt_main(db_path, enc_key)
    pages, applied, total = dec.apply_wal(wal_path, enc_key, pages)
    maxp = max(pages) if pages else 0
    with open(out_path, "wb") as f:
        for i in range(1, maxp + 1):
            f.write(pages.get(i, bytes(dec.PAGE)))
    con = sqlite3.connect(out_path)
    integrity = con.execute("PRAGMA integrity_check(1)").fetchone()[0]
    counts = {}
    try:
        counts["SnsTimeLine"] = con.execute("SELECT COUNT(*) FROM SnsTimeLine").fetchone()[0]
        counts["SnsMessage_tmp3"] = con.execute("SELECT COUNT(*) FROM SnsMessage_tmp3").fetchone()[0]
        counts["SnsTopItem_1"] = con.execute("SELECT COUNT(*) FROM SnsTopItem_1").fetchone()[0]
    except Exception as e:
        counts["error"] = repr(e)
    con.close()
    # Never persist or print the derived encryption key: it can always be
    # re-derived from the master key in config.json + the salt.
    return {"salt": salt.hex(),
            "page_count": maxp, "wal_frames_applied": applied, "wal_frames_total": total,
            "integrity_check": integrity, "table_counts": counts}


def load_targets():
    return wxconfig.targets()


def target_counts(plain_db):
    tg = load_targets()
    out = {}
    if not tg:
        return out
    for key, t in tg.items():
        wxid = t.get("wxid") if isinstance(t, dict) else None
        if not wxid:
            continue
        con = sqlite3.connect(plain_db)
        n = con.execute("SELECT COUNT(*) FROM SnsTimeLine WHERE user_name=?", (wxid,)).fetchone()[0]
        con.close()
        out[key] = {"wxid": wxid, "posts_in_db": n}
    return out


def append_index(entry, outdir=None):
    outdir = outdir or snapshots_dir()
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, "index.json")
    idx = []
    if os.path.exists(path):
        try:
            idx = json.load(open(path, encoding="utf-8"))
        except Exception:
            idx = []
    idx.append(entry)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def cmd_list():
    idx_path = os.path.join(snapshots_dir(), "index.json")
    idx = json.load(open(idx_path, encoding="utf-8")) if os.path.exists(idx_path) else []
    print("snapshots dir:", snapshots_dir())
    print("known snapshots: %d" % len(idx))
    for e in idx:
        print("  %(snapshot_id)s  created=%(created_at)s  pages=%(page_count)s  "
              "wal=%(wal_applied)s/%(wal_total)s  integrity=%(integrity_check)s  "
              "posts=%(sns_timeline_posts)s" % e)
    try:
        src = find_src_dir()
    except SystemExit:
        return
    print("\nlive source:", src)
    for name in ("sns.db", "sns.db-wal", "sns.db-shm"):
        p = os.path.join(src, name)
        if os.path.exists(p):
            print("  %-12s %12d B  mtime %s" % (
                name, os.path.getsize(p),
                datetime.fromtimestamp(os.path.getmtime(p)).isoformat(timespec="seconds")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="", help="sns db directory (default: auto-detect live WeChat)")
    ap.add_argument("--outdir", default="",
                    help="snapshot root (default: paths.snapshots_dir from config)")
    ap.add_argument("--label", default="", help="human readable label suffix")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list:
        cmd_list()
        return
    outdir = os.path.abspath(a.outdir) if a.outdir else snapshots_dir()
    src = os.path.abspath(a.src) if a.src else find_src_dir()
    now = datetime.now(em.TZ)
    sid = "sns_" + now.strftime("%Y%m%d_%H%M%S")
    label = a.label or now.strftime("%Y-%m-%d %H:%M:%S")
    snapdir = os.path.join(outdir, sid)
    if os.path.exists(snapdir):
        raise SystemExit("snapshot already exists: %s" % snapdir)
    rawdir = os.path.join(snapdir, "raw")
    print("[snapshot] id=%s src=%s" % (sid, src))
    files = copy_raw(src, rawdir)
    print("[snapshot] copied raw files: %s" % ", ".join(f["name"] for f in files))
    plain = os.path.join(snapdir, "sns_plain.db")
    info = decrypt_to_plain(os.path.join(rawdir, "sns.db"),
                            os.path.join(rawdir, "sns.db-wal"), plain)
    print("[snapshot] decrypt: pages=%d wal=%d/%d integrity=%s" % (
        info["page_count"], info["wal_frames_applied"], info["wal_frames_total"],
        info["integrity_check"]))
    tcounts = target_counts(plain)
    meta = {
        "snapshot_id": sid,
        "label": label,
        "created_at": now.isoformat(timespec="seconds"),
        "source_dir": src,
        "files": files,
        "decrypt": info,
        "sns_timeline_posts": info["table_counts"].get("SnsTimeLine"),
        "targets": tcounts,
        "note": "append-only snapshot; never modified by later sync runs",
    }
    with open(os.path.join(snapdir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    append_index({
        "snapshot_id": sid, "label": label, "created_at": meta["created_at"],
        "source_dir": src, "page_count": info["page_count"],
        "wal_applied": info["wal_frames_applied"], "wal_total": info["wal_frames_total"],
        "integrity_check": info["integrity_check"],
        "sns_timeline_posts": meta["sns_timeline_posts"],
        "targets": tcounts,
    }, outdir=outdir)
    print("[snapshot] wrote %s" % snapdir)
    print(json.dumps(meta, ensure_ascii=False, indent=1)[:1200])


if __name__ == "__main__":
    main()
