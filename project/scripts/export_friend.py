#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Export ONE friend's Moments from your own WeChat DB into an independent directory.

Incremental by default:
  * unions every available DB snapshot (legacy plaintext snapshot + project/data/snapshots/*)
  * merges with the existing JSON archive instead of overwriting it
  * keeps posts / likes / comments / media that are no longer visible in the live
    WeChat database (read-limit expired / deleted / re-posted) -> archived=True
  * only downloads media that is missing or previously failed; cached files are reused
  * writes a gzip backup of the previous JSON before every update

Usage:
  python3 export_friend.py --wxid <friend_wxid> --outdir deliverables_<name> \
      --title "<name>的朋友圈导出" --slug moments_<name> --xlsx "微信朋友圈_<name>.xlsx"
  python3 export_friend.py --wxid ... --no-merge        # first-time / forced fresh
  python3 export_friend.py --wxid ... --limit 3         # smoke test, fresh

Private values (master key, legacy DB path, snapshot/state dirs, display account)
come from project/config.json; friend targets are usually driven by
sync_friends.py using the same config.
"""
import os, sys, re, json, time, hashlib, ssl, base64, sqlite3, threading, argparse, glob, copy
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import moments_core as em
import archive_core as ac
import wxconfig
from isaac64 import ISAAC64

ROOT = em.ROOT
TZ = em.TZ
# Resolved from project/config.json inside main() so that --help works
# even before the config has been filled in.
DB = SELF = CONTACT_DB = SNAPSHOT_DIR = STATE_DIR = ""
VIDEO_XOR = 131072

CFG = {}
DELIV = None
MEDIA = RAWX = None
log_lock = threading.Lock()
_stats = {"done": 0, "ok": 0, "fail": 0, "bytes": 0, "retried_ok": 0, "retried_fail": 0}
_carry = {"ok": 0, "retried": 0, "missing_file": 0, "new": 0}

def log(msg):
    with log_lock:
        print(msg, flush=True)

def relp(path):
    return os.path.relpath(path, DELIV)

def sha_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def sanitize(s):
    return re.sub(r"[^0-9A-Za-z_-]+", "_", str(s or ""))[:60]

# ---------------------------------------------------------------- db discovery
def db_label(path):
    parent = os.path.basename(os.path.dirname(os.path.abspath(path)))
    return parent if parent else os.path.basename(os.path.abspath(path))

def discover_sources(explicit):
    specs = []
    if explicit:
        for p in explicit:
            p = os.path.abspath(p)
            if not os.path.exists(p):
                raise SystemExit("db not found: %s" % p)
            label = db_label(p)
            specs.append((label, p))
        return specs
    seen = set()
    def add(label, path):
        if not os.path.exists(path):
            return
        rp = os.path.realpath(path)
        if rp in seen:
            return
        seen.add(rp)
        specs.append((label, path))
    add(wxconfig.legacy_snapshot_label(), DB)
    if os.path.isdir(SNAPSHOT_DIR):
        for d in sorted(glob.glob(os.path.join(SNAPSHOT_DIR, "sns_*"))):
            p = os.path.join(d, "sns_plain.db")
            meta_p = os.path.join(d, "meta.json")
            label = os.path.basename(d)
            if os.path.exists(meta_p):
                try:
                    m = json.load(open(meta_p, encoding="utf-8"))
                    label = m.get("snapshot_id") or label
                except Exception:
                    pass
            add(label, p)
    if not specs:
        raise SystemExit("no plaintext DB available")
    return specs

# ---------------------------------------------------------------- contacts
def load_contacts():
    con = sqlite3.connect(CONTACT_DB)
    con.text_factory = bytes
    m = {}
    for row in con.execute("SELECT username, remark, nick_name, alias, remark_quan_pin FROM contact"):
        u = (row[0] or b"").decode("utf-8", "replace")
        if not u:
            continue
        dec = lambda b: (b or b"").decode("utf-8", "replace").strip()
        m[u] = {"remark": dec(row[1]), "nick_name": dec(row[2]),
                "alias": dec(row[3]), "remark_quan_pin": dec(row[4])}
    con.close()
    return m

def load_notice_names(db_path):
    m = {}
    try:
        con = sqlite3.connect(db_path)
        con.text_factory = bytes
        for row in con.execute("SELECT feed_id, from_username, from_nickname FROM SnsMessage_tmp3"):
            try:
                feed = int(row[0]) if row[0] is not None else None
            except Exception:
                continue
            u = (row[1] or b"").decode("utf-8", "replace")
            n = (row[2] or b"").decode("utf-8", "replace")
            if feed is not None and u and n:
                m.setdefault((feed, u), n)
        con.close()
    except Exception:
        pass
    return m

def make_display(username, xml_nickname, contacts, notice_map, post_tid):
    c = contacts.get(username) or {}
    remark = c.get("remark") or ""
    nick = c.get("nick_name") or notice_map.get((post_tid, username)) or ""
    xml_nickname = (xml_nickname or "").strip()
    if not nick and xml_nickname:
        nick = xml_nickname
    if remark and nick and remark != nick:
        display = "%s（%s）" % (remark, nick)
    elif remark:
        display = remark
    else:
        display = nick or xml_nickname
    return {"remark": remark, "wechat_nickname": nick, "display_name": display,
            "in_contacts": username in contacts,
            "notice_nickname": notice_map.get((post_tid, username)) or ""}

def enrich_person(d, contacts, notice_map, post_tid):
    d.update(make_display(d.get("username"), d.get("nickname"), contacts, notice_map, post_tid))
    return d

# ---------------------------------------------------------------- media fetch
EXT = em.EXT

def media_url(url, token):
    u = (url or "").replace("http://", "https://")
    u = re.sub(r"/150($|\?)", r"/0\1", u)
    if not token or "token=" in u:
        return u
    sep = "&" if "?" in u else "?"
    return u + sep + "token=" + token + "&idx=1"

def existing_file(outdir, fbase):
    if not fbase or not os.path.isdir(outdir):
        return None
    for name in sorted(os.listdir(outdir)):
        if name.startswith(fbase + "."):
            return os.path.join(outdir, name)
    return None

def xor_decode(data, key, swap):
    ks = ISAAC64(int(key), golden=0x9e3779b97f4a7c13).keystream(len(data), swap_words=swap)
    return bytes(a ^ b for a, b in zip(data, ks))

def save_bytes(dec, fmt, outdir, fbase):
    path = os.path.join(outdir, fbase + "." + EXT.get(fmt, "bin"))
    with open(path, "wb") as f:
        f.write(dec)
    return path

def fetch_image(url, token, key, outdir, fbase):
    full = media_url(url, token)
    data, headers = em.http_get(full)
    xenc = str(headers.get("X-Enc", "") or "").strip()
    key_s = str(key or "").strip()
    usable_key = key_s and key_s not in ("0", "")
    fmt = em.detect_format(data)
    dec = data
    if xenc == "1" and usable_key:
        dec = xor_decode(data, key_s, False)
        fmt = em.detect_format(dec)
    if fmt == "bin" and usable_key:
        dec2 = xor_decode(data, key_s, True)
        f2 = em.detect_format(dec2)
        if f2 != "bin":
            dec, fmt = dec2, f2
    dec = em.trim_media(dec, fmt)
    path = save_bytes(dec, fmt, outdir, fbase)
    return {"status": "ok" if fmt != "bin" else "decrypt-unverified", "format": fmt,
            "decrypted_bytes": len(dec), "sha256": hashlib.sha256(dec).hexdigest(),
            "file": relp(path), "download_url": full, "x_enc": xenc,
            "http_content_type": headers.get("Content-Type")}

def fetch_video(url, key, outdir, fbase, want_thumb=False, thumb_url=None):
    key_s = str(key or "").strip()
    usable_key = key_s and key_s not in ("0", "")
    if want_thumb:
        data, headers = em.http_get(thumb_url)
        fmt = em.detect_format(data)
        dec = data
        if usable_key and fmt == "bin":
            dec = xor_decode(data, key_s, False)
            fmt = em.detect_format(dec)
            if fmt == "bin":
                dec2 = xor_decode(data, key_s, True)
                if em.detect_format(dec2) != "bin":
                    dec, fmt = dec2, em.detect_format(dec2)
        dec = em.trim_media(dec, fmt)
        path = save_bytes(dec, fmt, outdir, fbase)
        return {"status": "ok" if fmt != "bin" else "unverified", "format": fmt,
                "decrypted_bytes": len(dec), "sha256": hashlib.sha256(dec).hexdigest(),
                "file": relp(path), "download_url": thumb_url}
    data, headers = em.http_get(url)
    xenc = str(headers.get("X-Enc", "") or "").strip()
    fmt = em.detect_format(data)
    dec = data
    if fmt != "mp4" and usable_key:
        n = min(len(data), VIDEO_XOR)
        ks = ISAAC64(int(key_s), golden=0x9e3779b97f4a7c13).keystream(n, swap_words=False)
        dec = bytes(a ^ b for a, b in zip(data[:n], ks)) + data[n:]
        fmt = em.detect_format(dec)
        if fmt != "mp4":
            ks = ISAAC64(int(key_s), golden=0x9e3779b97f4a7c13).keystream(n, swap_words=True)
            dec2 = bytes(a ^ b for a, b in zip(data[:n], ks)) + data[n:]
            if em.detect_format(dec2) == "mp4":
                dec, fmt = dec2, "mp4"
    path = save_bytes(dec, fmt, outdir, fbase)
    return {"status": "ok" if fmt == "mp4" else "unverified", "format": fmt,
            "decrypted_bytes": len(dec), "sha256": hashlib.sha256(dec).hexdigest(),
            "file": relp(path), "download_url": url, "x_enc": xenc,
            "http_content_type": headers.get("Content-Type"),
            "encrypted_prefix": min(len(data), VIDEO_XOR) if fmt == "mp4" and usable_key else 0}

# ---------------------------------------------------------------- merge helpers
CARRY_FIELDS = ("status", "format", "decrypted_bytes", "sha256", "file", "download_url",
                "x_enc", "http_content_type", "encrypted_prefix", "thumb", "thumb_file",
                "thumb_error", "recovery_note")

def rec_signatures(rec):
    sigs = []
    mid = str(rec.get("media_id") or "").strip()
    if mid and mid not in ("0", "None"):
        sigs.append(("id", mid))
    md5 = str(rec.get("md5_xml") or "").strip()
    url = str(rec.get("cdn_url") or "").strip()
    if md5:
        sigs.append(("md5", md5))
    if url:
        sigs.append(("url", url))
    return sigs

def find_old_rec(old_recs, used, rec):
    for sig in rec_signatures(rec):
        for i, o in enumerate(old_recs):
            if i in used:
                continue
            if sig in rec_signatures(o):
                used.add(i)
                return o
    return None

def carry_existing(rec, old_rec, post_archived):
    """Carry a previously downloaded file / status. Never carries a broken path."""
    fb = old_rec.get("file_base")
    if not fb:
        f = old_rec.get("file") or ""
        fb = os.path.splitext(os.path.basename(f))[0] if f else ""
    if fb:
        rec["file_base"] = fb
    prev = old_rec.get("status")
    if prev == "ok":
        fp = os.path.join(DELIV, old_rec["file"]) if old_rec.get("file") else ""
        if fp and os.path.exists(fp):
            for k in CARRY_FIELDS:
                if k in old_rec:
                    rec[k] = copy.deepcopy(old_rec[k])
            rec["cached"] = True
            rec["carried"] = True
            tf = old_rec.get("thumb_file")
            if tf and not os.path.exists(os.path.join(DELIV, tf)):
                rec.pop("thumb_file", None)
                rec.pop("thumb", None)
            _carry["ok"] += 1
            return "ok"
        rec["previous_status"] = "ok-file-missing"
        _carry["missing_file"] += 1
        return "retry"
    if prev is None:
        rec["previous_status"] = "new"
        _carry["new"] += 1
        return "new"
    rec["previous_status"] = prev
    rec["previous_error"] = old_rec.get("error")
    rec["previous_http_code"] = old_rec.get("http_code")
    if rec.get("status") is None and prev:
        rec["status"] = prev
        if post_archived:
            rec["recovery_note"] = ("历史归档帖（最新快照未包含），上次下载失败：%s"
                                    % (old_rec.get("error") or prev))
    rec["retry_attempted"] = True
    _carry["retried"] += 1
    return "retry"

# ---------------------------------------------------------------- main
def main():
    global DELIV, MEDIA, RAWX, DB, SELF, CONTACT_DB, SNAPSHOT_DIR, STATE_DIR
    ap = argparse.ArgumentParser()
    ap.add_argument("--wxid", required=True)
    ap.add_argument("--outdir", default="")
    ap.add_argument("--title", default="")
    ap.add_argument("--slug", default="")
    ap.add_argument("--xlsx", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=None,
                    help="concurrent media downloads (default: config export.workers)")
    ap.add_argument("--db", action="append", default=[], help="explicit plaintext sns DB (repeatable)")
    ap.add_argument("--no-merge", action="store_true", help="ignore existing archive JSON (dangerous)")
    ap.add_argument("--since", default="", help="only import posts created on/after this date (YYYY-MM-DD, UTC+8)")
    a = ap.parse_args()
    _paths = wxconfig.paths()
    DB = _paths["sns_plain"]
    SELF = wxconfig.self_wxid()
    CONTACT_DB = _paths["contact_plain"]
    SNAPSHOT_DIR = _paths["snapshots_dir"]
    STATE_DIR = _paths["state_dir"]
    if not a.workers:
        a.workers = wxconfig.workers(8)
    wxid = a.wxid.strip()
    since_ts = 0
    if a.since:
        since_dt = datetime.strptime(a.since, "%Y-%m-%d").replace(tzinfo=TZ)
        since_ts = int(since_dt.timestamp())
    merge = not a.no_merge
    if a.limit:
        merge = False

    t0 = time.time()
    contacts = load_contacts()
    me = contacts.get(wxid) or {}
    label = me.get("remark") or me.get("nick_name") or wxid
    outdir = a.outdir or ("deliverables_" + sanitize(me.get("remark_quan_pin") or wxid))
    DELIV = outdir if os.path.isabs(outdir) else os.path.join(ROOT, outdir)
    MEDIA = os.path.join(DELIV, "media")
    RAWX = os.path.join(DELIV, "raw_xmls")
    for d in (DELIV, MEDIA, RAWX):
        os.makedirs(d, exist_ok=True)
    slug = a.slug or ("moments_" + sanitize(me.get("remark_quan_pin") or "friend"))
    title = a.title or ("%s的朋友圈导出" % label)
    xlsx_name = a.xlsx or ("微信朋友圈_%s.xlsx" % label)
    CFG.update(dict(wxid=wxid, outdir=DELIV, slug=slug, title=title, xlsx=xlsx_name,
                    workers=a.workers, limit=a.limit, t0=t0, since=a.since, since_ts=since_ts))
    log("target: %s (%s), outdir=%s, title=%s" % (label, wxid, DELIV, title))

    notice_map = load_notice_names(DB)
    log("contacts: %d, notice names: %d" % (len(contacts), len(notice_map)))

    json_out = os.path.join(DELIV, slug + ".json")
    old_data = None
    old_posts = []
    if merge and os.path.exists(json_out):
        try:
            old_data = json.load(open(json_out, encoding="utf-8"))
            old_posts = old_data.get("posts") or []
            log("existing archive: %d posts, last generated %s" % (
                len(old_posts), (old_data.get("meta") or {}).get("generated_at_str")))
        except Exception as e:
            log("[WARN] cannot load existing archive JSON: %r" % (e,))
            old_posts = []
    if not merge:
        log("merge disabled -> fresh export")

    sources = discover_sources(a.db)
    log("source DBs: %s" % ", ".join(l for l, _ in sources))
    posts = old_posts
    source_runs = []
    for src_label, db_path in sources:
        con = sqlite3.connect(db_path)
        rows = con.execute("SELECT tid, content, pack_info_buf FROM SnsTimeLine WHERE user_name=?",
                           (wxid,)).fetchall()
        con.close()
        fresh = []
        for tid, content, pack in rows:
            xml = content if isinstance(content, str) else content.decode("utf-8", "replace")
            pack = pack if isinstance(pack, bytes) else (pack.encode() if pack else None)
            try:
                post = em.parse_post(tid, xml, pack)
            except Exception as e:
                log("PARSE FAIL db=%s tid=%s %r" % (src_label, tid, e))
                continue
            if since_ts and int(post.get("create_time") or 0) < since_ts:
                continue
            fresh.append(post)
        posts, stats = ac.merge_posts(posts, fresh, src_label, floor_ts=since_ts)
        stats.update(db=src_label, db_path=db_path, posts_in_snapshot=len(fresh))
        source_runs.append(stats)
        log("merge %-28s snapshot=%3d  archive=%3d  added=%d archived=%d" % (
            src_label, len(fresh), len(posts), stats["added"], stats["archived"]))
    if not posts:
        raise SystemExit("no posts found for %s" % wxid)

    # ---- stable filenames (old posts keep their existing paths; new posts get tid-suffixed names)
    for p in posts:
        idx = p["index"]
        date = datetime.fromtimestamp(p["create_time"], TZ).strftime("%Y-%m-%d_%H%M%S")
        have_dir = bool(p.get("media_dir"))
        have_xml = bool(p.get("raw_xml_file"))
        if not have_dir:
            dirname = "%03d_%s" % (idx, date)
            dpath = os.path.join(MEDIA, dirname)
            if os.path.exists(dpath):
                dirname = "%03d_%s_%s" % (idx, date, sanitize(str(p.get("tid"))[-6:]))
                dpath = os.path.join(MEDIA, dirname)
            p["media_dir"] = relp(dpath)
        os.makedirs(os.path.join(DELIV, p["media_dir"]), exist_ok=True)
        if not have_xml:
            rname = "post_%03d_%s.xml" % (idx, sanitize(str(p.get("tid") or p.get("tid_db"))[-8:]))
            p["raw_xml_file"] = relp(os.path.join(RAWX, rname))
        rpath = os.path.join(DELIV, p["raw_xml_file"])
        if (not p.get("archived")) and p.get("raw_xml"):
            with open(rpath, "w", encoding="utf-8") as f:
                f.write(p["raw_xml"])
        elif not os.path.exists(rpath) and p.get("raw_xml"):
            with open(rpath, "w", encoding="utf-8") as f:
                f.write(p["raw_xml"])

    # ---- build media records, carry previous downloads, schedule missing ones
    def media_enc_keys(xml):
        keys = []
        try:
            root2 = ET.fromstring(xml)
            for me_el in root2.findall(".//TimelineObject/ContentObject/mediaList/media"):
                e = me_el.find("enc")
                keys.append(e.attrib.get("key") if e is not None else None)
        except Exception:
            pass
        return keys

    old_by_key = {ac.post_key(p): p for p in old_posts}
    tasks = []
    finder_posts = []
    archived_indexes = []
    for p in posts:
        idx = p["index"]
        tid = p["tid_db"]
        if p.get("archived"):
            archived_indexes.append(idx)
        outdir_p = os.path.join(DELIV, p["media_dir"])
        oldp = old_by_key.get(ac.post_key(p)) if old_posts else None
        old_media = (oldp or {}).get("media_files") or []
        used_media = set()
        enc_keys = media_enc_keys(p.get("raw_xml") or "")
        recs = []
        for j, mm in enumerate(p.get("media") or []):
            mt = str(mm.get("type"))
            u = mm.get("url") if isinstance(mm.get("url"), dict) else {}
            attrs = u.get("attrs") or {}
            url = (u.get("text") or "").strip()
            token = attrs.get("token") or ""
            md5 = attrs.get("md5") or ""
            key = attrs.get("key") or ""
            thumb = mm.get("thumb") if isinstance(mm.get("thumb"), dict) else {}
            thumb_url = (thumb.get("text") or "").strip().replace("http://", "https://")
            size = mm.get("size") if isinstance(mm.get("size"), dict) else {}
            kind = "video" if mt == "6" else "image"
            fbase = ("video_%02d" if kind == "video" else "img_%02d") % (j + 1)
            external = bool(url) and not any(x in url for x in ("qpic.cn", "video.qq.com", "tc.qq.com"))
            rec = {"post_index": idx, "media_index": j + 1, "media_kind": kind,
                   "media_type": mt, "media_sub_type": mm.get("sub_type"),
                   "media_id": mm.get("id"), "cdn_url": url, "md5_xml": md5,
                   "key": key, "token": token, "size_xml": size,
                   "width_xml": size.get("width"), "height_xml": size.get("height"),
                   "total_size_xml": size.get("totalSize"),
                   "thumb_url": thumb_url, "video_duration": mm.get("videoDuration"),
                   "enc_key": enc_keys[j] if j < len(enc_keys) else None,
                   "file_base": fbase}
            if external:
                rec["status"] = "external"
                rec["recovery_note"] = "外部链接媒体（音乐/链接封面等，非朋友圈 CDN），未下载"
            old_rec = find_old_rec(old_media, used_media, rec) if old_media else None
            if old_rec:
                carry_existing(rec, old_rec, bool(p.get("archived")))
            else:
                _carry["new"] += 1
            recs.append(rec)
        # media entries that vanished from the live XML but were archived earlier
        for oi, old_rec in enumerate(old_media):
            if oi in used_media:
                continue
            rec = copy.deepcopy(old_rec)
            rec["post_index"] = idx
            rec["carried_from_archive"] = True
            recs.append(rec)
        p["media_files"] = recs

        # comment media
        old_cmedia = (oldp or {}).get("comment_media_files") or []
        used_cm = set()
        crecs = []
        for c in p.get("comments") or []:
            cid = c.get("comment_id")
            imgs = c.get("imagelist") or []
            for k, im in enumerate(imgs, 1):
                info = im if isinstance(im, dict) else {}
                def v(x):
                    return (x.get("text") if isinstance(x, dict) else x) or ""
                rec = {"post_index": idx, "comment_id": cid, "comment_media_index": k,
                       "cdn_url": v(info.get("url")), "key": v(info.get("key")),
                       "token": v(info.get("token")), "md5_xml": v(info.get("md5")),
                       "width_xml": v(info.get("width")), "height_xml": v(info.get("height")),
                       "media_id": v(info.get("media_id")),
                       "file_base": "comment_%s_img_%02d" % (sanitize(cid) or "x", k)}
                old_rec = find_old_rec(old_cmedia, used_cm, rec) if old_cmedia else None
                if old_rec:
                    carry_existing(rec, old_rec, bool(p.get("archived")))
                crecs.append(rec)
        for oi, old_rec in enumerate(old_cmedia):
            if oi in used_cm:
                continue
            rec = copy.deepcopy(old_rec)
            rec["post_index"] = idx
            rec["carried_from_archive"] = True
            crecs.append(rec)
        p["comment_media_files"] = crecs

        if p.get("finder_feed") is not None:
            finder_posts.append(idx)
        if p.get("archived"):
            # 历史归档帖：仍尝试补齐“从未下载过”的媒体（首次合并历史快照时），
            # 但不反复重试已知失败/已存在的媒体，避免每次同步都打 CDN。
            def _transient_failure(r):
                if r.get("previous_status") != "download-failed":
                    return False
                err = str(r.get("previous_error") or "")
                return any(s in err for s in ("SSL", "EOF", "timed out",
                                              "Connection reset", "Connection aborted"))
            def _never_tried(r):
                if r.get("status") not in ("ok", "external") and r.get("previous_status") in (None, "new"):
                    return True
                return _transient_failure(r)
            recs = [r for r in recs if _never_tried(r)]
            crecs = [r for r in crecs if _never_tried(r)]
            if not recs and not crecs:
                continue
        for rec in recs:
            if rec.get("status") == "external":
                continue
            if rec.get("status") == "ok" and rec.get("file") and os.path.exists(os.path.join(DELIV, rec["file"])):
                continue
            kind = "video" if rec.get("media_kind") == "video" else "image"
            tasks.append({"kind": kind, "rec": rec, "outdir": outdir_p,
                          "fbase": rec.get("file_base") or ("video_%02d" % rec["media_index"]),
                          "url": rec.get("cdn_url"), "token": rec.get("token") or "",
                          "key": rec.get("enc_key") or rec.get("key") or "",
                          "thumb_url": rec.get("thumb_url")})
        for rec in crecs:
            if rec.get("status") == "external":
                continue
            if rec.get("status") == "ok" and rec.get("file") and os.path.exists(os.path.join(DELIV, rec["file"])):
                continue
            tasks.append({"kind": "comment-image", "rec": rec, "outdir": outdir_p,
                          "fbase": rec.get("file_base") or ("comment_x_img_%02d" % rec["comment_media_index"]),
                          "url": rec.get("cdn_url"), "token": rec.get("token") or "",
                          "key": rec.get("key") or ""})
    log("download tasks: %d (images %d, videos %d, comment images %d; carried %d, retry %d)" % (
        len(tasks), sum(1 for t in tasks if t["kind"] == "image"),
        sum(1 for t in tasks if t["kind"] == "video"),
        sum(1 for t in tasks if t["kind"] == "comment-image"),
        _carry["ok"], _carry["retried"]))

    def run_task(t):
        rec = t["rec"]
        existing = existing_file(t["outdir"], t["fbase"])
        if existing:
            b = open(existing, "rb").read()
            fmt = em.detect_format(b)
            if fmt != "bin":
                rec.update({"status": "ok", "format": fmt, "decrypted_bytes": len(b),
                            "sha256": hashlib.sha256(b).hexdigest(), "file": relp(existing),
                            "cached": True})
                for _k in ("recovery_note", "error"):
                    rec.pop(_k, None)
                if rec.get("previous_status"):
                    rec["recovered"] = True
                    with log_lock:
                        _stats["retried_ok"] += 1
                if t["kind"] == "video":
                    ex_thumb = existing_file(t["outdir"], t["fbase"] + "_thumb")
                    if ex_thumb:
                        tb = open(ex_thumb, "rb").read()
                        rec["thumb_file"] = relp(ex_thumb)
                        rec["thumb"] = {"status": "ok", "format": em.detect_format(tb),
                                        "decrypted_bytes": len(tb),
                                        "sha256": hashlib.sha256(tb).hexdigest(),
                                        "file": relp(ex_thumb),
                                        "download_url": t.get("thumb_url")}
                with log_lock:
                    _stats["done"] += 1
                    _stats["ok"] += 1
                    _stats["bytes"] += len(b)
                return rec
        try:
            if t["kind"] in ("image", "comment-image"):
                r = fetch_image(t["url"], t["token"], t["key"], t["outdir"], t["fbase"])
                rec.update(r)
            else:
                r = fetch_video(t["url"], t["key"], t["outdir"], t["fbase"])
                rec.update(r)
            if rec.get("status") == "ok":
                for _k in ("recovery_note", "error"):
                    rec.pop(_k, None)
                if t.get("thumb_url"):
                    try:
                        tr = fetch_video(t["url"], t["key"], t["outdir"], t["fbase"] + "_thumb",
                                         want_thumb=True, thumb_url=t["thumb_url"])
                        rec["thumb_file"] = tr.get("file")
                        rec["thumb"] = tr
                    except Exception as e:
                        rec["thumb_error"] = repr(e)
            with log_lock:
                _stats["done"] += 1
                if rec.get("status") == "ok":
                    _stats["ok"] += 1
                    _stats["bytes"] += rec.get("decrypted_bytes") or 0
                    if rec.get("previous_status"):
                        rec["recovered"] = True
                        _stats["retried_ok"] += 1
                else:
                    _stats["fail"] += 1
                    if rec.get("previous_status"):
                        _stats["retried_fail"] += 1
                n = _stats["done"]
            if n % 50 == 0:
                log("progress %d/%d ok=%d fail=%d bytes=%.1fMB elapsed=%.0fs" % (
                    n, len(tasks), _stats["ok"], _stats["fail"], _stats["bytes"] / 1e6,
                    time.time() - t0))
        except Exception as e:
            rec.update({"status": "download-failed", "error": repr(e)})
            with log_lock:
                _stats["done"] += 1
                _stats["fail"] += 1
                if rec.get("previous_status"):
                    _stats["retried_fail"] += 1
            log("FAIL %s/%s: %r" % (t["rec"]["post_index"], t["fbase"], e))
        return rec

    with ThreadPoolExecutor(max_workers=max(1, a.workers)) as ex:
        futures = [ex.submit(run_task, t) for t in tasks]
        for f in as_completed(futures):
            f.result()
    log("downloads finished in %.0fs: ok=%d fail=%d bytes=%.1fMB (retry ok=%d fail=%d)" % (
        time.time() - t0, _stats["ok"], _stats["fail"], _stats["bytes"] / 1e6,
        _stats["retried_ok"], _stats["retried_fail"]))

    # ---- enrich likes/comments (display name = remark（wechat nickname）)
    for p in posts:
        tid = p["tid_db"]
        for l in p.get("likes") or []:
            enrich_person(l, contacts, notice_map, tid)
        for c in p.get("comments") or []:
            enrich_person(c, contacts, notice_map, tid)
        by_id = {str(c.get("comment_id")): c for c in (p.get("comments") or [])}
        for c in p.get("comments") or []:
            rid = str(c.get("ref_comment_id") or "0")
            if rid not in ("0", "") and rid in by_id:
                r = by_id[rid]
                c["ref_comment"] = {"username": r.get("username"),
                                    "nickname": r.get("nickname"),
                                    "display_name": r.get("display_name"),
                                    "content": r.get("content"),
                                    "create_time_str": r.get("create_time_str")}
        p["like_count"] = len(p.get("likes") or [])
        p["comment_count"] = len(p.get("comments") or [])
        p["media_count"] = len(p.get("media") or [])

    # ---- rebuild flat lists + meta
    all_media = [rec for p in posts for rec in (p.get("media_files") or [])]
    all_cmedia = [rec for p in posts for rec in (p.get("comment_media_files") or [])]
    visible_posts = [p for p in posts if not p.get("archived")]
    visible_failed = [m for m in all_media if m.get("status") not in ("ok", "external")
                      and not (posts[m["post_index"] - 1].get("archived") if 0 < m.get("post_index", 0) <= len(posts) else False)]
    failed_posts = sorted({m["post_index"] for m in all_media if m.get("status") not in ("ok", "external")} |
                          {m["post_index"] for m in all_cmedia if m.get("status") not in ("ok", "external")})
    ghost_posts = sorted({m["post_index"] for m in all_media if m.get("status") not in ("ok", "external")
                          and not posts[m["post_index"] - 1].get("archived")} |
                         {m["post_index"] for m in all_cmedia if m.get("status") not in ("ok", "external")
                          and not posts[m["post_index"] - 1].get("archived")})
    type_count = {}
    for p in posts:
        tp = p.get("content_object_type_name") or "未知"
        type_count[tp] = type_count.get(tp, 0) + 1
    my = contacts.get(SELF) or {}
    my_label = my.get("nick_name") or "本人"
    header_account = "对象：%s（%s） · wxid：%s · 导出账号：%s（%s）" % (
        label, me.get("nick_name") or "", wxid, my_label, SELF)
    scope_note = ("仅导出 %s 本人发布的微信朋友圈（好友视角：点赞/评论为对方帖子下的互动）。"
                  "视频号（finderFeed）内容微信未开放下载，仅保留元数据与原始 XML。" % label)
    now = time.time()
    now_str = em.ts2str(now)
    now_iso = datetime.fromtimestamp(now, TZ).isoformat(timespec="seconds")
    old_meta = (old_data or {}).get("meta") or {}
    first_exported_at = old_meta.get("first_exported_at") or old_meta.get("generated_at") or now_iso
    runs = list(old_meta.get("export_runs") or [])
    if not runs and old_meta:
        runs.append({"run_at": old_meta.get("generated_at") or first_exported_at,
                     "note": "initial export (pre-incremental)"})
    run_entry = {
        "run_at": now_iso,
        "source_dbs": [l for l, _ in sources],
        "snapshots": source_runs,
        "posts_in_visible_snapshot": sum(r.get("posts_in_snapshot", 0) for r in source_runs),
        "archive_posts_after_merge": len(posts),
        "added": sum(r.get("added", 0) for r in source_runs),
        "newly_archived": sum(r.get("newly_archived", 0) for r in source_runs),
        "reactivated": sum(r.get("reactivated", 0) for r in source_runs),
        "media_downloaded": _stats["ok"],
        "media_failed": _stats["fail"],
        "media_retried_ok": _stats["retried_ok"],
        "media_retried_fail": _stats["retried_fail"],
        "media_carried": _carry["ok"],
    }
    runs.append(run_entry)
    runs = runs[-30:]
    snapshot_ids = [l for l, _ in sources if l.startswith("sns_")]
    meta = {
        "generated_at": em.ts2iso(now),
        "generated_at_str": now_str,
        "timezone": "Asia/Singapore (UTC+8)",
        "source_db": " + ".join(l for l, _ in sources),
        "source_dbs": [l for l, _ in sources],
        "source_snapshots": snapshot_ids,
        "contact_db": wxconfig.config_path_value(CONTACT_DB),
        "target_wxid": wxid,
        "target_nickname": me.get("nick_name") or None,
        "target_remark": me.get("remark") or None,
        "target_alias": me.get("alias") or None,
        "target_remark_quan_pin": me.get("remark_quan_pin") or None,
        "account_wxid": SELF,
        "account_nickname": my_label,
        "account_remark": my.get("remark") or "",
        "header_account": header_account,
        "report_title": title,
        "html_filename": slug + ".html",
        "md_filename": slug + ".md",
        "xlsx_filename": xlsx_name,
        "scope_note": scope_note,
        "range": {"start": posts[0]["create_time_str"], "end": posts[-1]["create_time_str"]},
        "visible_range": ({"start": visible_posts[0]["create_time_str"],
                           "end": visible_posts[-1]["create_time_str"]} if visible_posts else None),
        "post_count": len(posts),
        "visible_post_count": len(visible_posts),
        "archived_post_count": len(archived_indexes),
        "archived_posts": archived_indexes,
        "invisible_posts_retained": len(archived_indexes),
        "content_type_count": type_count,
        "media_count": len(all_media),
        "image_count": sum(1 for r in all_media if r.get("media_kind") == "image"),
        "video_count": sum(1 for r in all_media if r.get("media_kind") == "video"),
        "media_downloaded": sum(1 for r in all_media if r.get("status") == "ok"),
        "media_failed": sum(1 for r in all_media if r.get("status") not in ("ok", "external")),
        "media_external": sum(1 for r in all_media if r.get("status") == "external"),
        "comment_media_count": len(all_cmedia),
        "comment_media_downloaded": sum(1 for r in all_cmedia if r.get("status") == "ok"),
        "like_count": sum(len(p.get("likes") or []) for p in posts),
        "comment_count": sum(len(p.get("comments") or []) for p in posts),
        "media_bytes": sum(r.get("decrypted_bytes") or 0 for r in all_media if r.get("status") == "ok"),
        "comment_media_bytes": sum(r.get("decrypted_bytes") or 0 for r in all_cmedia if r.get("status") == "ok"),
        "ghost_posts": ghost_posts,
        "finder_posts": finder_posts,
        "display_rule": "点赞/评论显示名 = 备注（微信昵称）；无备注时仅显示微信昵称/昵称",
        "since": a.since or "",
        "since_ts": int(since_ts or 0),
        "incremental": True,
        "merge_policy": "只增不删：每次同步按 tid 合并全部历史快照与旧 JSON；未出现在最新快照的帖子也保留（archived=True，仅表示最新快照未包含，不等于已删除）；旧媒体文件只复用不覆盖；更新前备份旧 JSON" + ("；增量导入起点 %s（更早历史不追溯，但已归档内容保留）" % a.since if a.since else ""),
        "first_exported_at": first_exported_at,
        "last_updated_at": now_iso,
        "run_count": len(runs),
        "export_runs": runs,
        "media_carried": _carry["ok"],
        "media_retry_attempted": _carry["retried"],
        "media_retried_ok": _stats["retried_ok"],
        "media_retried_fail": _stats["retried_fail"],
    }
    data = {"meta": meta, "posts": posts, "media": all_media, "comment_media": all_cmedia}
    backup = None
    if merge and os.path.exists(json_out):
        backup = ac.backup_json(json_out, os.path.join(STATE_DIR, slug), keep=10)
    with open(json_out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    log("wrote %s (%.1fMB)%s" % (json_out, os.path.getsize(json_out) / 1e6,
                                 ("  backup=" + backup) if backup else ""))
    log("SUMMARY posts=%d visible=%d archived=%d images=%d videos=%d likes=%d comments=%d "
        "cmedia=%d/%d failed_posts=%s finder=%s" % (
            len(posts), len(visible_posts), len(archived_indexes),
            meta["image_count"], meta["video_count"], meta["like_count"],
            meta["comment_count"], meta["comment_media_downloaded"],
            len(all_cmedia), ghost_posts, finder_posts))
    log("ALL DONE in %.0fs" % (time.time() - t0))

if __name__ == "__main__":
    main()
