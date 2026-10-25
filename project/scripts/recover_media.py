#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Recover failed media by XML-md5 cross-post dedup, then flag unrecoverable ones.

Ghost posts (deleted & reposted moments) keep XML rows whose CDN media were purged
(HTTP 404). Where an identical md5 exists in another still-live post, copy the
already-decrypted file over and mark recovered_from.
"""
import json, os, shutil, hashlib, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wxconfig

ROOT = wxconfig.ROOT
_SELF_EXPORT = wxconfig.self_export()
DELIV = _SELF_EXPORT["outdir"]
J = json.load(open(_SELF_EXPORT["json"], encoding="utf-8"))
posts = J["posts"]
media = J["media"]

def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

ok_by_md5 = {}
for m in media:
    if m.get("status") == "ok" and m.get("md5_xml"):
        ok_by_md5.setdefault(m["md5_xml"], []).append(m)

EXT = {"jpeg": "jpg", "png": "png", "gif": "gif", "webp": "webp"}
recovered, unrecoverable = 0, 0
for p in posts:
    for rec in p.get("media_files") or []:
        if rec.get("status") == "ok":
            continue
        srcs = ok_by_md5.get(rec.get("md5_xml") or "") or []
        if not srcs:
            continue
        src = srcs[0]
        fmt = src.get("format") or "jpeg"
        ext = EXT.get(fmt, "jpg")
        srcpath = os.path.join(DELIV, src["file"])
        outdir = os.path.join(DELIV, "media", os.path.basename(p["media_dir"]))
        os.makedirs(outdir, exist_ok=True)
        target = os.path.join(outdir, "img_%02d.%s" % (rec["media_index"], ext))
        shutil.copy2(srcpath, target)
        upd = {"status": "ok", "format": fmt,
               "decrypted_bytes": os.path.getsize(target),
               "sha256": sha256_file(target),
               "file": os.path.relpath(target, DELIV),
               "recovered_from": {"post_index": src["post_index"], "media_index": src["media_index"]},
               "recovery_note": "CDN 404（原帖已删除/重发）；同一 XML md5 的媒体在另一帖仍有效，已按字节完全一致复制",
               "cached": True}
        rec.update(upd)
        recovered += 1
        # mirror into flat media list
        for m in media:
            if m["post_index"] == rec["post_index"] and m["media_index"] == rec["media_index"]:
                m.update(upd)

# flag leftovers
ghost_posts = {87, 111, 208, 264, 285}
for p in posts:
    for rec in p.get("media_files") or []:
        if rec.get("status") == "ok":
            continue
        unrecoverable += 1
        if p["index"] in ghost_posts:
            rec["recovery_note"] = "原帖疑似已删除并重发：微信服务器已清理媒体文件（HTTP 404），且无同 md5 副本可恢复"
        else:
            rec["recovery_note"] = "CDN 返回 404/400，无法恢复"
        for m in media:
            if m["post_index"] == rec["post_index"] and m["media_index"] == rec["media_index"]:
                m["recovery_note"] = rec["recovery_note"]

all_media = [m for p in posts for m in (p.get("media_files") or [])]
for p in posts:
    p["media_downloaded"] = sum(1 for r in p["media_files"] if r.get("status") == "ok")
    p["media_failed"] = sum(1 for r in p["media_files"] if r.get("status") != "ok")
J["meta"]["media_downloaded"] = sum(1 for r in all_media if r.get("status") == "ok")
J["meta"]["media_failed"] = sum(1 for r in all_media if r.get("status") != "ok")
J["meta"]["media_bytes"] = sum(r.get("decrypted_bytes") or 0 for r in all_media if r.get("status") == "ok")
J["meta"]["recovery_note"] = "%d 个媒体通过跨帖同 md5 恢复；%d 个无法恢复（原帖已删除/重发，CDN 已清理）" % (recovered, unrecoverable)
J["meta"]["ghost_posts"] = sorted(ghost_posts)
with open(_SELF_EXPORT["json"], "w", encoding="utf-8") as f:
    json.dump(J, f, ensure_ascii=False, indent=1)
print("recovered", recovered, "unrecoverable", unrecoverable)
print("meta:", J["meta"]["media_downloaded"], "/", J["meta"]["media_count"], "failed", J["meta"]["media_failed"])
