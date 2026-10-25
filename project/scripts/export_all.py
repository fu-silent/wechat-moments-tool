#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Export all Moments published by the configured self account.

Reads every private value (master key, paths, account, titles, output names)
from project/config.json. Adds:
  - contact remark / wechat nickname enrichment -> display name "remark（nickname）"
  - video download + decrypt (first 128KB XOR, rest plaintext) + cover thumb
  - concurrent downloads with resume
Outputs the self deliverable configured in project/config.json
(folder / JSON / HTML / Markdown / XLSX names, master key, DB and contact paths).
"""
import os, sys, re, json, time, hashlib, ssl, base64, sqlite3, threading
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import moments_core as em
import wxconfig
from isaac64 import ISAAC64

ROOT = em.ROOT
TZ = em.TZ
_SELF_EXPORT = wxconfig.self_export()
_PATHS = wxconfig.paths()
SELF = wxconfig.self_wxid()
DB = _PATHS["sns_plain"]
CONTACT_DB = _PATHS["contact_plain"]
DELIV = _SELF_EXPORT["outdir"]
MEDIA = os.path.join(DELIV, "media")
RAWX = os.path.join(DELIV, "raw_xmls")
JSON_OUT = _SELF_EXPORT["json"]
REPORT_TITLE = _SELF_EXPORT["title"]
HTML_NAME = os.path.basename(_SELF_EXPORT["html"])
MD_NAME = os.path.basename(_SELF_EXPORT["md"])
XLSX_NAME = os.path.basename(_SELF_EXPORT["xlsx"])
VIDEO_XOR = 131072
WORKERS = wxconfig.workers(8)
LIMIT = int(os.environ.get("LIMIT_POSTS", "0"))  # >0 for smoke test

for d in (DELIV, MEDIA, RAWX):
    os.makedirs(d, exist_ok=True)

log_lock = threading.Lock()
_counters = {"done": 0, "ok": 0, "fail": 0, "bytes": 0}

def log(msg):
    with log_lock:
        print(msg, flush=True)

def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

# ---------------------------------------------------------------- contacts
def load_contacts():
    con = sqlite3.connect(CONTACT_DB)
    con.text_factory = bytes
    m = {}
    for row in con.execute("SELECT username, remark, nick_name, alias FROM contact"):
        u = (row[0] or b"").decode("utf-8", "replace")
        if not u:
            continue
        m[u] = {
            "remark": (row[1] or b"").decode("utf-8", "replace").strip(),
            "nick_name": (row[2] or b"").decode("utf-8", "replace").strip(),
            "alias": (row[3] or b"").decode("utf-8", "replace").strip(),
        }
    con.close()
    return m

def load_notice_names():
    con = sqlite3.connect(DB)
    con.text_factory = bytes
    m = {}
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
    return {
        "remark": remark,
        "wechat_nickname": nick,
        "display_name": display,
        "in_contacts": username in contacts,
        "notice_nickname": notice_map.get((post_tid, username)) or "",
    }

def enrich_person(d, contacts, notice_map, post_tid):
    x = d.get("nickname")
    d.update(make_display(d.get("username"), x, contacts, notice_map, post_tid))
    return d

# ---------------------------------------------------------------- media fetch
EXT = em.EXT

def existing_file(outdir, fbase):
    if not os.path.isdir(outdir):
        return None
    for name in os.listdir(outdir):
        if name.startswith(fbase + "."):
            return os.path.join(outdir, name)
    return None

def fetch_image(url, token, key, outdir, fbase):
    full = em.build_url(url, token)
    data, headers = em.http_get(full)
    xenc = str(headers.get("X-Enc", "")).strip()
    dec = em.isaac_decrypt(data, int(key)) if (xenc == "1" and key) else data
    fmt = em.detect_format(dec)
    if fmt == "bin" and key:
        ks = ISAAC64(int(key), golden=0x9e3779b97f4a7c13).keystream(len(data), swap_words=True)
        dec2 = bytes(a ^ b for a, b in zip(data, ks))
        f2 = em.detect_format(dec2)
        if f2 != "bin":
            dec, fmt = dec2, f2
    dec = em.trim_media(dec, fmt)
    path = os.path.join(outdir, fbase + "." + EXT[fmt])
    with open(path, "wb") as f:
        f.write(dec)
    return {"status": "ok" if fmt != "bin" else "decrypt-unverified", "format": fmt,
            "decrypted_bytes": len(dec), "sha256": hashlib.sha256(dec).hexdigest(),
            "file": os.path.relpath(path, DELIV), "download_url": full,
            "x_enc": xenc, "http_content_type": headers.get("Content-Type")}

def fetch_video(url, key, outdir, fbase, want_thumb=False, thumb_url=None):
    # video: XOR first 128KB only; thumb: full XOR
    if want_thumb:
        data, headers = em.http_get(thumb_url)
        n = len(data)
        ks = ISAAC64(int(key), golden=0x9e3779b97f4a7c13).keystream(n, swap_words=False)
        dec = bytes(a ^ b for a, b in zip(data, ks))
        fmt = em.detect_format(dec)
        if fmt == "bin":
            ks = ISAAC64(int(key), golden=0x9e3779b97f4a7c13).keystream(n, swap_words=True)
            dec2 = bytes(a ^ b for a, b in zip(data, ks))
            f2 = em.detect_format(dec2)
            if f2 != "bin":
                dec, fmt = dec2, f2
        dec = em.trim_media(dec, fmt)
        path = os.path.join(outdir, fbase + "." + EXT[fmt])
        with open(path, "wb") as f:
            f.write(dec)
        return {"status": "ok" if fmt != "bin" else "unverified", "format": fmt,
                "decrypted_bytes": len(dec), "sha256": hashlib.sha256(dec).hexdigest(),
                "file": os.path.relpath(path, DELIV), "download_url": thumb_url}
    data, headers = em.http_get(url)
    n = min(len(data), VIDEO_XOR)
    ks = ISAAC64(int(key), golden=0x9e3779b97f4a7c13).keystream(n, swap_words=False)
    dec = bytes(a ^ b for a, b in zip(data[:n], ks)) + data[n:]
    fmt = em.detect_format(dec)
    if fmt != "mp4":
        ks = ISAAC64(int(key), golden=0x9e3779b97f4a7c13).keystream(n, swap_words=True)
        dec2 = bytes(a ^ b for a, b in zip(data[:n], ks)) + data[n:]
        if em.detect_format(dec2) == "mp4":
            dec, fmt = dec2, "mp4"
    path = os.path.join(outdir, fbase + "." + EXT.get(fmt, "mp4"))
    with open(path, "wb") as f:
        f.write(dec)
    return {"status": "ok" if fmt == "mp4" else "unverified", "format": fmt,
            "decrypted_bytes": len(dec), "sha256": hashlib.sha256(dec).hexdigest(),
            "file": os.path.relpath(path, DELIV), "download_url": url,
            "x_enc": headers.get("X-Enc"), "http_content_type": headers.get("Content-Type"),
            "encrypted_prefix": n}

# ---------------------------------------------------------------- main
def main():
    t0 = time.time()
    log("loading contacts / notices ...")
    contacts = load_contacts()
    notice_map = load_notice_names()
    log("contacts: %d, notice names: %d" % (len(contacts), len(notice_map)))

    con = sqlite3.connect(DB)
    rows = con.execute("SELECT tid, content, pack_info_buf FROM SnsTimeLine WHERE user_name=?", (SELF,)).fetchall()
    con.close()
    posts = []
    for tid, content, pack in rows:
        xml = content if isinstance(content, str) else content.decode("utf-8", "replace")
        pack = pack if isinstance(pack, bytes) else (pack.encode() if pack else None)
        try:
            p = em.parse_post(tid, xml, pack)
        except Exception as e:
            log("PARSE FAIL tid=%s %r" % (tid, e))
            continue
        posts.append((p, xml))
    posts.sort(key=lambda x: x[0]["create_time"])
    if LIMIT:
        posts = posts[-LIMIT:]
        log("SMOKE TEST: limited to last %d posts" % LIMIT)
    log("self posts: %d, span %s -> %s" % (len(posts), posts[0][0]["create_time_str"], posts[-1][0]["create_time_str"]))

    # build post list + pending download tasks
    out_posts = []
    tasks = []
    for i, (p, xml) in enumerate(posts):
        idx = i + 1
        p["index"] = idx
        d = datetime.fromtimestamp(p["create_time"], TZ).strftime("%Y-%m-%d_%H%M%S")
        dirname = "%03d_%s" % (idx, d)
        p["media_dir"] = os.path.relpath(os.path.join(MEDIA, dirname), DELIV)
        os.makedirs(os.path.join(MEDIA, dirname), exist_ok=True)
        with open(os.path.join(RAWX, "post_%03d.xml" % idx), "w", encoding="utf-8") as f:
            f.write(xml)
        p["raw_xml_file"] = os.path.relpath(os.path.join(RAWX, "post_%03d.xml" % idx), DELIV)
        out_posts.append(p)

    # enumerate media tasks
    def media_enc_keys(xml):
        root2 = ET.fromstring(xml)
        keys = []
        for me in root2.findall(".//TimelineObject/ContentObject/mediaList/media"):
            e = me.find("enc")
            keys.append(e.attrib.get("key") if e is not None else None)
        return keys

    xml_by_idx = {}
    for (pp, xx) in posts:
        xml_by_idx[pp["index"]] = xx

    for p in out_posts:
        idx = p["index"]
        outdir = os.path.join(MEDIA, os.path.basename(p["media_dir"]))
        enc_keys = media_enc_keys(xml_by_idx[idx])
        p["media_files"] = []
        for j, mm in enumerate(p["media"]):
            mt = str(mm.get("type"))
            u = mm.get("url") if isinstance(mm.get("url"), dict) else {}
            attrs = u.get("attrs") or {}
            url = (u.get("text") or "").strip()
            token = attrs.get("token") or ""
            md5 = attrs.get("md5") or ""
            key = attrs.get("key") or ""
            thumb = mm.get("thumb") if isinstance(mm.get("thumb"), dict) else {}
            thumb_url = (thumb.get("text") or "").strip().replace("http://", "https://")
            size = mm.get("size") or {}
            rec = {
                "post_index": idx, "media_index": j + 1, "media_kind": "video" if mt == "6" else "image",
                "media_id": mm.get("id"), "media_type": mt, "media_sub_type": mm.get("sub_type"),
                "cdn_url": url, "md5_xml": md5, "key": key, "size_xml": size,
                "width_xml": size.get("width"), "height_xml": size.get("height"),
                "total_size_xml": size.get("totalSize"),
                "thumb_url": thumb_url, "video_duration": mm.get("videoDuration"),
            }
            # enc key for video: taken directly from <enc key="..."> in XML
            rec["enc_key"] = enc_keys[j] if j < len(enc_keys) else None
            p["media_files"].append(rec)
            fbase = "img_%02d" % (j + 1)
            if mt == "6":
                fbase = "video_%02d" % (j + 1)
                tasks.append({"kind": "video", "rec": rec, "outdir": outdir, "fbase": fbase,
                              "url": url, "key": rec.get("enc_key"), "thumb_url": thumb_url})
            else:
                tasks.append({"kind": "image", "rec": rec, "outdir": outdir, "fbase": fbase,
                              "url": url, "token": token, "key": key})
        # comment images
        ci = 0
        for c in p["comments"]:
            for im in (c.get("imagelist") or []):
                info = im if isinstance(im, dict) else {}
                def v(x):
                    return (x.get("text") if isinstance(x, dict) else x) or ""
                ci += 1
                cid = c.get("comment_id")
                rec = {"post_index": idx, "comment_id": cid, "comment_media_index": ci,
                       "cdn_url": v(info.get("url")), "key": v(info.get("key")),
                       "token": v(info.get("token")), "md5_xml": v(info.get("md5")),
                       "width_xml": v(info.get("width")), "height_xml": v(info.get("height")),
                       "media_id": v(info.get("media_id"))}
                p["comment_media_files"] = p.get("comment_media_files", []) + [rec]
                tasks.append({"kind": "comment-image", "rec": rec, "outdir": outdir,
                              "fbase": "comment_%s_img_%02d" % (cid, ci),
                              "url": rec["cdn_url"], "token": rec["token"], "key": rec["key"]})
    log("download tasks: %d (images %d, videos %d, comment images %d)" % (
        len(tasks),
        sum(1 for t in tasks if t["kind"] == "image"),
        sum(1 for t in tasks if t["kind"] == "video"),
        sum(1 for t in tasks if t["kind"] == "comment-image")))

    # run downloads
    def run_task(t):
        rec = t["rec"]
        existing = existing_file(t["outdir"], t["fbase"])
        if existing:
            b = open(existing, "rb").read()
            fmt = em.detect_format(b)
            rec.update({"status": "ok", "format": fmt, "decrypted_bytes": len(b),
                        "sha256": hashlib.sha256(b).hexdigest(),
                        "file": os.path.relpath(existing, DELIV), "cached": True})
            if t["kind"] == "video":
                ex_thumb = existing_file(t["outdir"], t["fbase"] + "_thumb")
                if ex_thumb:
                    tb = open(ex_thumb, "rb").read()
                    rec["thumb_file"] = os.path.relpath(ex_thumb, DELIV)
                    rec["thumb"] = {"status": "ok", "format": em.detect_format(tb),
                                    "decrypted_bytes": len(tb),
                                    "sha256": hashlib.sha256(tb).hexdigest(),
                                    "file": os.path.relpath(ex_thumb, DELIV),
                                    "download_url": t.get("thumb_url")}
            with log_lock:
                _counters["done"] += 1
                _counters["ok"] += 1
                _counters["bytes"] += len(b)
            return rec
        try:
            if t["kind"] == "image":
                r = fetch_image(t["url"], t["token"], t["key"], t["outdir"], t["fbase"])
            elif t["kind"] == "comment-image":
                r = fetch_image(t["url"], t["token"], t["key"], t["outdir"], t["fbase"])
            else:
                r = fetch_video(t["url"], t["key"], t["outdir"], t["fbase"])
                rec.update(r)
                if t.get("thumb_url") and t["key"]:
                    try:
                        tr = fetch_video(t["url"], t["key"], t["outdir"], t["fbase"] + "_thumb",
                                         want_thumb=True, thumb_url=t["thumb_url"])
                        rec["thumb_file"] = tr.get("file")
                        rec["thumb"] = tr
                    except Exception as e:
                        rec["thumb_error"] = repr(e)
                with log_lock:
                    _counters["done"] += 1
                    if rec.get("status") == "ok":
                        _counters["ok"] += 1
                        _counters["bytes"] += rec.get("decrypted_bytes") or 0
                    else:
                        _counters["fail"] += 1
                return rec
            rec.update(r)
            with log_lock:
                _counters["done"] += 1
                if rec.get("status") == "ok":
                    _counters["ok"] += 1
                    _counters["bytes"] += rec.get("decrypted_bytes") or 0
                else:
                    _counters["fail"] += 1
                n = _counters["done"]
            if n % 50 == 0:
                log("progress %d/%d ok=%d fail=%d bytes=%.1fMB elapsed=%.0fs" % (
                    n, len(tasks), _counters["ok"], _counters["fail"],
                    _counters["bytes"] / 1e6, time.time() - t0))
        except Exception as e:
            rec.update({"status": "download-failed", "error": repr(e)})
            with log_lock:
                _counters["done"] += 1
                _counters["fail"] += 1
                n = _counters["done"]
            log("FAIL %s/%s: %r (done=%d)" % (t["rec"]["post_index"], t["fbase"], e, n))
        return rec

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = [ex.submit(run_task, t) for t in tasks]
        for f in as_completed(futures):
            f.result()
    log("downloads finished in %.0fs: ok=%d fail=%d bytes=%.1fMB" % (
        time.time() - t0, _counters["ok"], _counters["fail"], _counters["bytes"] / 1e6))

    # enrich likes/comments with display names
    for p in out_posts:
        tid = p["tid_db"]
        for l in p["likes"]:
            enrich_person(l, contacts, notice_map, tid)
        for c in p["comments"]:
            enrich_person(c, contacts, notice_map, tid)
        by_id = {str(c.get("comment_id")): c for c in p["comments"]}
        for c in p["comments"]:
            rid = str(c.get("ref_comment_id") or "0")
            if rid not in ("0", "") and rid in by_id:
                r = by_id[rid]
                c["ref_comment"] = {"username": r.get("username"), "nickname": r.get("nickname"),
                                    "display_name": r.get("display_name"), "content": r.get("content"),
                                    "create_time_str": r.get("create_time_str")}

    all_media = [rec for p in out_posts for rec in p["media_files"]]
    all_cmedia = [rec for p in out_posts for rec in (p.get("comment_media_files") or [])]
    data = {
        "meta": {
            "generated_at": em.ts2iso(time.time()),
            "generated_at_str": em.ts2str(time.time()),
            "timezone": "Asia/Singapore (UTC+8)",
            "source_db": wxconfig.config_path_value(DB) + " (WeChat 4.x sns.db + WAL)",
            "contact_db": wxconfig.config_path_value(CONTACT_DB),
            "report_title": REPORT_TITLE,
            "html_filename": HTML_NAME,
            "md_filename": MD_NAME,
            "xlsx_filename": XLSX_NAME,
            "account_wxid": SELF,
            "account_nickname": out_posts[-1].get("nickname") if out_posts else None,
            "range": {"start": out_posts[0]["create_time_str"], "end": out_posts[-1]["create_time_str"]},
            "post_count": len(out_posts),
            "media_count": len(all_media),
            "image_count": sum(1 for r in all_media if r.get("media_kind") == "image"),
            "video_count": sum(1 for r in all_media if r.get("media_kind") == "video"),
            "media_downloaded": sum(1 for r in all_media if r.get("status") == "ok"),
            "media_failed": sum(1 for r in all_media if r.get("status") not in ("ok",)),
            "comment_media_count": len(all_cmedia),
            "comment_media_downloaded": sum(1 for r in all_cmedia if r.get("status") == "ok"),
            "like_count": sum(p["like_count"] for p in out_posts),
            "comment_count": sum(p["comment_count"] for p in out_posts),
            "media_bytes": sum(r.get("decrypted_bytes") or 0 for r in all_media if r.get("kind") != "thumb"),
            "display_rule": "点赞/评论显示名 = 备注（微信昵称）；无备注时仅显示微信昵称/昵称",
        },
        "posts": out_posts,
        "media": all_media,
        "comment_media": all_cmedia,
    }
    with open(JSON_OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    log("wrote %s (%.1fMB)" % (JSON_OUT, os.path.getsize(JSON_OUT) / 1e6))
    log("ALL DONE in %.0fs" % (time.time() - t0))

if __name__ == "__main__":
    main()