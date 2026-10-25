#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared core for WeChat 4.x Moments export.

Machine-independent core: paths come from project/config.json, HTTP/CDN fetch,
ISAAC-64 media decryption, XML parsing (TimelineObject + LocalExtraInfo).
Used by export_all.py / export_friend.py; input DB produced by decrypt_sns.py.
"""
import os, sys, re, time, ssl, base64
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isaac64 import ISAAC64
import wxconfig

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TZ = timezone(timedelta(hours=8))


def db_path():
    """Legacy plaintext sns DB (the base archive every sync unions into)."""
    return wxconfig.paths()["sns_plain"]


def self_wxid():
    """The WeChat account running this export (from config)."""
    return wxconfig.self_wxid()

WEEKDAY = ["周一","周二","周三","周四","周五","周六","周日"]
CONTENT_TYPES = {"1":"图片","2":"纯文字","3":"链接/音乐","7":"视频号","15":"视频","26":"视频号","28":"视频号","34":"?","54":"图片"}
COMMENT_TYPE = {"0":"文字","2":"图片"}

ctx = ssl._create_unverified_context()

def ts2str(t):
    return datetime.fromtimestamp(int(t), TZ).strftime("%Y-%m-%d %H:%M:%S")
def ts2iso(t):
    return datetime.fromtimestamp(int(t), TZ).isoformat()
def weekday(t):
    return WEEKDAY[datetime.fromtimestamp(int(t), TZ).weekday()]

def http_get(url, timeout=40, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "MicroMessenger Client", "Accept": "*/*", "Connection": "keep-alive"})
            resp = urllib.request.urlopen(req, timeout=timeout, context=ctx)
            return resp.read(), dict(resp.headers)
        except Exception as e:
            last = e
            time.sleep(1.2 * (i + 1))
    raise last

def build_url(url, token, thumb=False):
    u = url.replace("http://", "https://")
    if not thumb:
        u = re.sub(r"/150($|\?)", r"/0\1", u)
    if not token:
        return u
    sep = "&" if "?" in u else "?"
    return u + sep + "token=" + token + "&idx=1"

def detect_format(b):
    if b[:3] == b"\xff\xd8\xff": return "jpeg"
    if b[:8] == b"\x89PNG\r\n\x1a\n": return "png"
    if b[:4] == b"GIF8": return "gif"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP": return "webp"
    if b[4:8] == b"ftyp" and b[8:12] in (b"heic", b"heix", b"mif1", b"msf1"): return "heic"
    if b[4:8] == b"ftyp": return "mp4"
    if b[:4] == b"wxgf": return "wxgf"
    return "bin"

def trim_media(b, fmt):
    if fmt == "jpeg":
        i = b.rfind(b"\xff\xd9")
        if i > 0: return b[:i+2]
    if fmt == "png":
        i = b.rfind(b"IEND")
        if i > 0: return b[:i+8]
    return b

def isaac_decrypt(data, key_int):
    ks = ISAAC64(int(key_int), golden=0x9e3779b97f4a7c13).keystream(len(data), swap_words=False)
    return bytes(a ^ c for a, c in zip(data, ks))

EXT = {"jpeg":"jpg","png":"png","gif":"gif","webp":"webp","heic":"heic","mp4":"mp4","wxgf":"wxgf","bin":"bin"}

def parse_media_el(media_el, depth=0):
    m = {}
    for ch in media_el:
        tag = ch.tag
        if tag in ("thumb", "url"):
            m[tag] = {"attrs": dict(ch.attrib), "text": (ch.text or "").strip()}
        elif tag == "size":
            m["size"] = dict(ch.attrib)
        elif len(ch):
            m[tag] = parse_media_el(ch, depth+1) if tag == "livePhoto" else raw_children(ch)
        else:
            m[tag] = (ch.text or "").strip()
    return m

def raw_children(el):
    """Generic recursive serialization: leaf -> text, node -> dict/list."""
    kids = list(el)
    if not kids:
        txt = (el.text or "").strip()
        if el.attrib:
            return {"text": txt, "attrs": dict(el.attrib)}
        return txt
    out = {"attrs": dict(el.attrib)} if el.attrib else {}
    groups = {}
    for k in kids:
        groups.setdefault(k.tag, []).append(raw_children(k))
    for k, v in groups.items():
        out[k] = v[0] if len(v) == 1 else v
    return out

def parse_comment(uc):
    d = {}
    for ch in uc:
        if ch.tag == "imagelist":
            d["imagelist"] = [raw_children(x) for x in ch] if len(ch) else []
        elif ch.tag == "emojilist":
            d["emojilist"] = [raw_children(x) for x in ch] if len(ch) else []
        elif len(ch):
            d[ch.tag] = raw_children(ch)
        else:
            d[ch.tag] = (ch.text or "").strip()
    if "create_time" in d and d["create_time"]:
        try:
            d["create_time_str"] = ts2str(int(d["create_time"]))
        except Exception:
            pass
    if "comment_content_type" in d:
        d["comment_content_type_name"] = COMMENT_TYPE.get(d["comment_content_type"], "未知")
    return d

def parse_post(tid_signed, xml, pack_info_buf):
    root = ET.fromstring(xml)
    to = root.find("TimelineObject")
    lei = root.find("LocalExtraInfo")
    p = {}
    p["tid_db"] = tid_signed
    p["pack_info_buf_b64"] = base64.b64encode(pack_info_buf).decode() if pack_info_buf else None
    def g(tag, parent):
        e = parent.find(tag) if parent is not None else None
        return (e.text or "").strip() if e is not None and e.text is not None else None
    p["tid"] = g("id", to)
    p["username"] = g("username", to)
    p["nickname"] = g("nickname", lei)
    ct = int(g("createTime", to))
    p["create_time"] = ct
    p["create_time_str"] = ts2str(ct)
    p["create_time_iso"] = ts2iso(ct)
    p["weekday"] = weekday(ct)
    p["content"] = g("contentDesc", to) or ""
    for tag in ("contentDescShowType","contentDescScene","sightFolded","showFlag","private",
                "publicUserName","publicBrandContactType","sourceUserName","isTop","guideTop",
                "sourceNickName","statisticsData","statExtStr","canvasInfoXml",
                "publicPhotoAccountUserName","publicPhotoAccountNickName"):
        p[tag] = g(tag, to)
    loc = to.find("location")
    if loc is not None and dict(loc.attrib):
        a = dict(loc.attrib)
        p["location"] = {
            "city": a.get("city"), "country": a.get("country"),
            "poiName": a.get("poiName"), "poiAddress": a.get("poiAddress"),
            "poiAddressName": a.get("poiAddressName"),
            "poiClassifyId": a.get("poiClassifyId"), "poiClassifyType": a.get("poiClassifyType"),
            "poiScale": a.get("poiScale"), "poiClickableStatus": a.get("poiClickableStatus"),
            "latitude": a.get("longitude"), "longitude": a.get("latitude"),
            "raw_attrs": a,
            "note": "XML 原始属性 latitude/longitude 的值互换了，这里已换回：latitude=纬度, longitude=经度",
        }
    else:
        p["location"] = None
    co = to.find("ContentObject")
    p["content_object_type"] = g("type", co)
    p["content_object_type_name"] = CONTENT_TYPES.get(p["content_object_type"], "未知")
    p["content_sub_style"] = g("contentSubStyle", co)
    p["content_title"] = g("title", co)
    p["content_url"] = g("contentUrl", co)
    ff = co.find("finderFeed") if co is not None else None
    p["finder_feed"] = raw_children(ff) if ff is not None else None
    p["media"] = []
    ml = co.find("mediaList") if co is not None else None
    if ml is not None:
        for me in ml.findall("media"):
            p["media"].append(parse_media_el(me))
    p["media_count"] = len(p["media"])
    for tag in ("appInfo","actionInfo","weappInfo","streamvideo"):
        e = to.find(tag)
        p[tag] = raw_children(e) if e is not None else None
    p["likes"] = []
    p["comments"] = []
    if lei is not None:
        lul = lei.find("like_user_list")
        if lul is not None:
            for uc in lul.findall("user_comment"):
                d = {}
                for ch in uc:
                    d[ch.tag] = (ch.text or "").strip()
                if d.get("create_time"):
                    d["create_time_str"] = ts2str(int(d["create_time"]))
                p["likes"].append(d)
        cul = lei.find("comment_user_list")
        if cul is not None:
            for uc in cul.findall("user_comment"):
                p["comments"].append(parse_comment(uc))
    p["like_count"] = len(p["likes"])
    p["comment_count"] = len(p["comments"])
    # resolve ref comments
    by_id = {str(c.get("comment_id")): c for c in p["comments"]}
    for c in p["comments"]:
        rid = str(c.get("ref_comment_id") or "0")
        if rid not in ("0", "") and rid in by_id:
            r = by_id[rid]
            c["ref_comment"] = {"nickname": r.get("nickname"), "content": r.get("content"),
                                "create_time_str": r.get("create_time_str"),
                                "username": r.get("username")}
    p["raw_xml"] = xml
    return p
