#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generic WeChat 4.x SQLCipher db decrypt (reuses decrypt_sns primitives).

The master key is read from project/config.json; paths can also come from config:
  wechat.contact_db_encrypted  -> default --db
  paths.contact_plain          -> default --out

Usage:
  python3 decrypt_db.py                                  # contact.db from config
  python3 decrypt_db.py --db /path/contact.db --out /path/contact_plain.db
  python3 decrypt_db.py <encrypted.db> <output.db> [wal]
"""
import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import decrypt_sns as ds
import wxconfig


def decrypt(db_path, wal_path, out_path):
    p1 = open(db_path, "rb").read(ds.PAGE)
    salt = p1[:ds.SALT_SZ]
    enc_key = ds.derive_enc_key(ds.passphrase(), salt)
    ok = ds.verify_page1(enc_key, p1)
    print("db:", db_path)
    print("salt:", salt.hex(), "page1_hmac:", ok)
    if not ok:
        raise SystemExit("[ERROR] page1 HMAC mismatch: " + db_path)
    pages = ds.decrypt_main(db_path, enc_key)
    pages, applied, total = ds.apply_wal(wal_path, enc_key, pages)
    print("pages:", len(pages), "wal applied:", applied, "of", total)
    maxp = max(pages)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "wb") as f:
        for i in range(1, maxp + 1):
            f.write(pages.get(i, bytes(ds.PAGE)))
    con = sqlite3.connect(out_path)
    print("integrity:", con.execute("PRAGMA integrity_check(3)").fetchone()[0])
    tabs = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    print("tables:", tabs[:40])
    con.close()


def main():
    ap = argparse.ArgumentParser(description="decrypt one WeChat 4.x SQLCipher DB using config.json")
    ap.add_argument("db_pos", nargs="?", default="", help="encrypted DB (positional form)")
    ap.add_argument("out_pos", nargs="?", default="", help="output plaintext DB (positional form)")
    ap.add_argument("wal_pos", nargs="?", default="", help="WAL file (positional form)")
    ap.add_argument("--db", dest="db_opt", default="",
                    help="encrypted DB (default: wechat.contact_db_encrypted)")
    ap.add_argument("--out", dest="out_opt", default="",
                    help="output plaintext DB (default: paths.contact_plain)")
    ap.add_argument("--wal", dest="wal_opt", default="", help="WAL file (default: <db>-wal)")
    a = ap.parse_args()
    cfg_paths = wxconfig.paths()
    db = a.db_opt or a.db_pos or wxconfig.contact_db_encrypted()
    if not db:
        raise SystemExit(
            "未指定加密数据库，且 config 中 wechat.contact_db_encrypted 为空。\n"
            "用法: python3 decrypt_db.py --db <微信contact.db> --out <输出路径>")
    out = a.out_opt or a.out_pos or cfg_paths["contact_plain"]
    wal = a.wal_opt or a.wal_pos or (db + "-wal")
    decrypt(os.path.abspath(db), os.path.abspath(wal), os.path.abspath(out))


if __name__ == "__main__":
    main()
