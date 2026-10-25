#!/usr/bin/env python3
"""Decrypt WeChat 4.x (macOS 4.1.15) SQLCipher sns.db + WAL into plaintext SQLite.

Params confirmed empirically on this machine:
  enc_key  = PBKDF2-HMAC-SHA512(passphrase=32-byte master key, salt=file salt, 256000 iters, 32B)
  MAC key  = PBKDF2-HMAC-SHA512(enc_key, salt ^ 0x3a, 2 iters, 32B)
  cipher   = AES-256-CBC, page 4096, reserve 80 = IV(16) + HMAC-SHA512(64)
  page 1   = page[4096-salt...], IV at [4016:4032], HMAC at [4032:4096]
"""
import hashlib, hmac, struct, os, sys
from Crypto.Cipher import AES

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wxconfig

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = 4096
RESERVE = 80
IV_SZ = 16
HMAC_SZ = 64
SALT_SZ = 16
SQLITE_HDR = b"SQLite format 3" + bytes(1)
Z80 = bytes(RESERVE)


def passphrase():
    """32-byte WeChat master key, read from project/config.json (never hardcode)."""
    return wxconfig.master_key_bytes()

def derive_enc_key(passphrase, salt):
    return hashlib.pbkdf2_hmac("sha512", passphrase, salt, 256000, dklen=32)

def mac_key(enc_key, salt):
    return hashlib.pbkdf2_hmac("sha512", enc_key, bytes(b ^ 0x3a for b in salt), 2, dklen=32)

def verify_page1(enc_key, page1):
    salt = page1[:SALT_SZ]
    mk = mac_key(enc_key, salt)
    hdata = page1[SALT_SZ:PAGE - RESERVE + IV_SZ]
    stored = page1[PAGE - RESERVE + IV_SZ:PAGE]
    return hmac.new(mk, hdata + struct.pack("<I", 1), hashlib.sha512).digest() == stored

def decrypt_page(enc_key, page, pgno):
    iv = page[PAGE - RESERVE:PAGE - RESERVE + IV_SZ]
    if pgno == 1:
        body = AES.new(enc_key, AES.MODE_CBC, iv).decrypt(page[SALT_SZ:PAGE - RESERVE])
        return SQLITE_HDR + body + Z80
    body = AES.new(enc_key, AES.MODE_CBC, iv).decrypt(page[:PAGE - RESERVE])
    return body + Z80

def decrypt_main(db_path, enc_key):
    data = open(db_path, "rb").read()
    n = len(data) // PAGE
    pages = {}
    for i in range(n):
        pages[i + 1] = decrypt_page(enc_key, data[i * PAGE:(i + 1) * PAGE], i + 1)
    return pages

def apply_wal(wal_path, enc_key, pages):
    if not os.path.exists(wal_path):
        return pages, 0, 0
    w = open(wal_path, "rb").read()
    if len(w) < 32:
        return pages, 0, 0
    magic, version, pgsz, ckpt, salt1, salt2, c1, c2 = struct.unpack(">IIIIIIII", w[:32])
    if pgsz != PAGE:
        print("[WARN] WAL page size %d" % pgsz, file=sys.stderr)
    frame_sz = 24 + PAGE
    nframes = (len(w) - 32) // frame_sz
    frames = []
    last_commit = -1
    for i in range(nframes):
        off = 32 + i * frame_sz
        pgno, dbsize, fs1, fs2, fc1, fc2 = struct.unpack(">IIIIII", w[off:off + 24])
        if fs1 != salt1 or fs2 != salt2:
            break
        frames.append((pgno, w[off + 24:off + 24 + PAGE]))
        if dbsize != 0:
            last_commit = i
    applied = 0
    for i in range(last_commit + 1):
        pgno, raw = frames[i]
        if pgno < 1:
            continue
        pages[pgno] = decrypt_page(enc_key, raw, pgno)
        applied += 1
    return pages, applied, last_commit + 1

def main():
    cfg_paths = wxconfig.paths()
    db_path = sys.argv[1] if len(sys.argv) > 1 else cfg_paths["raw_sns_db"]
    wal_path = sys.argv[2] if len(sys.argv) > 2 else db_path + "-wal"
    out_path = sys.argv[3] if len(sys.argv) > 3 else cfg_paths["sns_plain"]
    p1 = open(db_path, "rb").read(PAGE)
    salt = p1[:SALT_SZ]
    enc_key = derive_enc_key(passphrase(), salt)
    print("salt:", salt.hex())
    print("master key: loaded from config (%s)" % wxconfig.config_path())
    if not verify_page1(enc_key, p1):
        print("[ERROR] page1 HMAC mismatch")
        sys.exit(1)
    print("[OK] page1 HMAC verified")
    pages = decrypt_main(db_path, enc_key)
    print("decrypted %d main pages" % len(pages))
    pages, applied, total = apply_wal(wal_path, enc_key, pages)
    print("applied %d of %d WAL frames" % (applied, total))
    maxp = max(pages)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "wb") as f:
        for i in range(1, maxp + 1):
            f.write(pages.get(i, bytes(PAGE)))
    import sqlite3
    con = sqlite3.connect(out_path)
    print("integrity:", con.execute("PRAGMA integrity_check(5)").fetchone())
    tabs = [r[0] for r in con.execute("SELECT name, type FROM sqlite_master")]
    print("%d objects" % len(tabs))
    con.close()

if __name__ == "__main__":
    main()
