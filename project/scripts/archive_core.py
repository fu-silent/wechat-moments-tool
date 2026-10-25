#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Incremental merge primitives for Moments exports.

Core guarantee: a later sync can ADD posts / likes / comments / media and update
their content, but it can never DELETE something that was exported before. Posts
that are no longer visible in the current WeChat database (read-limit expired,
deleted, re-posted) are kept and flagged archived=True.

Merge keys
  post     : unsigned TimelineObject <id> (fallback: signed tid_db)
  like     : (username, create_time)
  comment  : comment_id if present, else (username, create_time, content)
  media    : media <id> if present, else (url, type)
"""
import copy, gzip, json, os, shutil

def post_key(p):
    tid = p.get("tid")
    if tid not in (None, ""):
        return str(tid)
    return "signed:" + str(p.get("tid_db"))

def like_key(l):
    return (str(l.get("username") or ""), str(l.get("create_time") or ""))

def comment_key(c):
    cid = str(c.get("comment_id") or "").strip()
    if cid and cid not in ("0", "None"):
        return "cid:" + cid
    return "body:%s|%s|%s" % (c.get("username") or "", c.get("create_time") or "",
                              c.get("content") or "")

def media_key(m):
    mid = str(m.get("id") or "").strip()
    if mid and mid not in ("0", "None"):
        return "id:" + mid
    u = m.get("url") if isinstance(m.get("url"), dict) else {}
    return "url:%s|%s" % (u.get("text") or "", m.get("type") or "")

def _num(v):
    try:
        return int(float(v))
    except Exception:
        return 0

def _nonempty(v):
    return v is not None and v != "" and v != [] and v != {}

def deep_merge(old, new):
    """new wins on conflicts; nested dicts merged recursively."""
    if isinstance(old, dict) and isinstance(new, dict):
        out = copy.deepcopy(old)
        for k, v in new.items():
            if k in out and isinstance(out[k], dict) and isinstance(v, dict):
                out[k] = deep_merge(out[k], v)
            else:
                out[k] = copy.deepcopy(v)
        return out
    return copy.deepcopy(new)

def merge_people(old_list, new_list, keyfn, label):
    """Union of like / comment dicts. Old records are never dropped; new values win."""
    out, order, old_by = {}, [], {}
    for person in old_list or []:
        k = keyfn(person)
        if k not in out:
            out[k] = copy.deepcopy(person)
            order.append(k)
        old_by[k] = person
    added = 0
    for person in new_list or []:
        k = keyfn(person)
        if k not in out:
            out[k] = copy.deepcopy(person)
            order.append(k)
            added += 1
        else:
            merged = copy.deepcopy(out[k])
            for f, v in person.items():
                if _nonempty(v):
                    merged[f] = copy.deepcopy(v)
                elif f not in merged:
                    merged[f] = copy.deepcopy(v)
            if label == "likes":
                merged.setdefault("first_seen_at", merged.get("first_seen_at"))
            out[k] = merged
    people = [out[k] for k in order]
    people.sort(key=lambda x: (_num(x.get("create_time")),))
    return people, added

def merge_media_xml(old_list, new_list):
    """Union of XML <media> entries; latest XML wins, old entries retained."""
    out, order = {}, []
    for m in old_list or []:
        k = media_key(m)
        if k not in out:
            out[k] = copy.deepcopy(m)
            order.append(k)
    added = 0
    for m in new_list or []:
        k = media_key(m)
        if k not in out:
            out[k] = copy.deepcopy(m)
            order.append(k)
            added += 1
        else:
            out[k] = deep_merge(out[k], m)
    return [out[k] for k in order], added

SCALAR_FIELDS = [
    "username", "nickname", "content", "contentDescShowType", "contentDescScene",
    "sightFolded", "showFlag", "private", "publicUserName", "publicBrandContactType",
    "sourceUserName", "isTop", "guideTop", "sourceNickName", "statisticsData",
    "statExtStr", "canvasInfoXml", "publicPhotoAccountUserName",
    "publicPhotoAccountNickName", "content_object_type", "content_object_type_name",
    "content_sub_style", "content_title", "content_url", "finder_feed",
    "appInfo", "actionInfo", "weappInfo", "streamvideo", "raw_xml",
]

def merge_post(old, new, source_label, now_iso):
    """Return (merged_post, changed). Either old or new must be provided."""
    if old is None:
        p = copy.deepcopy(new)
        p["first_seen_at"] = now_iso
        p["last_seen_at"] = now_iso
        p["sources"] = [source_label]
        p["archived"] = False
        p["archived_note"] = None
        return p, True
    if new is None:
        p = copy.deepcopy(old)
        if not p.get("archived"):
            p["archived"] = True
            p["archived_since"] = now_iso
            p["archived_note"] = ("该帖子未出现在最新微信快照中（可能因朋友圈可读期限到期、客户端缓存清理或已删除/重发），"
                                   "已从历史快照完整保留（含已下载媒体），后续快照若重新出现会自动转为可见。"
                                   "原判断：可读期限到期 / 已删除 / 发布后重发；旧说明："
                                  "本条及已下载媒体为历史归档，保留不删。")
        p["last_seen_at"] = p.get("last_seen_at")
        return p, False
    p = copy.deepcopy(old)
    before = (len(p.get("likes") or []), len(p.get("comments") or []),
              len(p.get("media") or []), p.get("content"))
    for f in SCALAR_FIELDS:
        if f in new and _nonempty(new.get(f)):
            p[f] = copy.deepcopy(new.get(f))
        elif f not in p:
            p[f] = copy.deepcopy(new.get(f))
    # create time / tid should never move for the same post, but keep newest value
    for f in ("tid", "tid_db", "create_time", "create_time_str", "create_time_iso",
              "weekday", "pack_info_buf_b64"):
        if f in new and _nonempty(new.get(f)):
            p[f] = copy.deepcopy(new.get(f))
    likes, lk_added = merge_people(p.get("likes"), new.get("likes"), like_key, "likes")
    comments, c_added = merge_people(p.get("comments"), new.get("comments"), comment_key, "comments")
    media, m_added = merge_media_xml(p.get("media"), new.get("media"))
    p["likes"], p["comments"], p["media"] = likes, comments, media
    p["like_count"], p["comment_count"], p["media_count"] = len(likes), len(comments), len(media)
    p["last_seen_at"] = now_iso
    sources = list(p.get("sources") or [])
    if source_label not in sources:
        sources.append(source_label)
    p["sources"] = sources
    if p.get("archived"):
        p["previously_archived"] = True
        p["archived"] = False
        p["archived_note"] = None
        p.pop("archived_since", None)
        changed = True
    else:
        after = (len(likes), len(comments), len(media), p.get("content"))
        changed = (lk_added + c_added + m_added > 0) or before != after or True
        # raw_xml / like timestamps can update without count change; treat every
        # reappearance in a snapshot as an update so export_runs records it.
        changed = True
    return p, changed

def merge_posts(existing_posts, fresh_posts, source_label, now_iso=None, floor_ts=0):
    """Merge all posts from one fresh DB snapshot into the accumulated archive.

    existing_posts: list from archive JSON (may be empty)
    fresh_posts:    list of parse_post() results from one DB
    Returns (posts, stats). Post indexes are NOT assigned here.
    """
    from datetime import datetime
    if now_iso is None:
        now_iso = datetime.now().astimezone().isoformat(timespec="seconds")
    old_map = {}
    for p in existing_posts or []:
        old_map[post_key(p)] = p
    fresh_keys = set()
    merged, stats = [], {"added": 0, "updated": 0, "unchanged": 0,
                         "reactivated": 0, "newly_archived": 0, "archived": 0,
                         "fresh_posts": len(fresh_posts or [])}
    for new in fresh_posts or []:
        k = post_key(new)
        fresh_keys.add(k)
        old = old_map.get(k)
        p, changed = merge_post(old, new, source_label, now_iso)
        if old is None:
            stats["added"] += 1
        elif old.get("archived"):
            stats["reactivated"] += 1
            stats["updated"] += 1
        elif changed:
            stats["updated"] += 1
        else:
            stats["unchanged"] += 1
        merged.append(p)
    old_order = []
    for p in existing_posts or []:
        k = post_key(p)
        if k in fresh_keys:
            continue
        mp, _ = merge_post(p, None, source_label, now_iso)
        if floor_ts and _num(p.get("create_time")) < floor_ts:
            if not p.get("archived"):
                mp["archived"] = False
                for _k in ("archived_note", "archived_since", "previously_archived"):
                    mp.pop(_k, None)
            old_order.append(mp)
            continue
        if not p.get("archived"):
            stats["newly_archived"] += 1
        old_order.append(mp)
    merged.extend(old_order)
    stats["archived"] = sum(1 for p in merged if p.get("archived"))
    merged.sort(key=lambda p: (_num(p.get("create_time")), str(p.get("tid") or p.get("tid_db"))))
    for i, p in enumerate(merged):
        p["index"] = i + 1
    return merged, stats

def backup_json(json_path, backup_dir, keep=10):
    """gzip a copy of the previous archive JSON before rewriting it."""
    if not os.path.exists(json_path):
        return None
    from datetime import datetime
    os.makedirs(backup_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(backup_dir, "backup_%s.json.gz" % ts)
    tmp = dst + ".tmp"
    with open(json_path, "rb") as src, gzip.open(tmp, "wb") as out:
        shutil.copyfileobj(src, out)
    os.replace(tmp, dst)
    backups = sorted(x for x in os.listdir(backup_dir)
                     if x.startswith("backup_") and x.endswith(".json.gz"))
    for old in backups[:-keep]:
        try:
            os.remove(os.path.join(backup_dir, old))
        except OSError:
            pass
    return dst
