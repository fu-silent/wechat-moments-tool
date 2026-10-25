#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build HTML/Markdown/CSV deliverables for the configured self Moments export.
Like/comment display rule: 备注（微信名） — remark first, wechat nickname in brackets.
Defaults (folder / slug / title) come from project/config.json when available;
WX_DELIV / WX_DATA environment variables still override them (used by sync_friends).
"""
import os, json, csv, html
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    import wxconfig
    _SELF = wxconfig.self_export() if wxconfig.load(required=False) is not None else {}
except SystemExit:
    _SELF = {}
DELIV = (os.environ.get("WX_DELIV") or _SELF.get("outdir")
         or os.path.join(ROOT, "deliverables"))
DATA = (os.environ.get("WX_DATA") or _SELF.get("json")
        or os.path.join(DELIV, "moments_all.json"))
REPORT = DELIV
ALLD = os.path.join(DELIV, "csv")
os.makedirs(ALLD, exist_ok=True)
d = json.load(open(DATA, encoding="utf-8"))
meta = d["meta"]
TITLE = meta.get("report_title") or _SELF.get("title") or "我的朋友圈全量导出"
HTML_NAME = meta.get("html_filename") or os.path.basename(_SELF.get("html") or "moments_all.html")
MD_NAME = meta.get("md_filename") or os.path.basename(_SELF.get("md") or "moments_all.md")
posts = sorted(d["posts"], key=lambda p: p["create_time"])
E = html.escape

def rel(path):
    return path.replace(os.sep, "/")

def fmt_size(n):
    n = int(n or 0)
    if n > 1024 * 1024:
        return "%.1f MB" % (n / 1048576.0)
    if n > 1024:
        return "%.0f KB" % (n / 1024.0)
    return "%d B" % n

def person(p):
    return p.get("display_name") or p.get("wechat_nickname") or p.get("nickname") or ""

def like_rows_html(p):
    if not p["likes"]:
        return '<div class="none">（无点赞）</div>'
    rows = []
    for i, l in enumerate(p["likes"], 1):
        rows.append('<tr><td>%d</td><td class="nm">%s</td><td>%s</td></tr>'
                    % (i, E(person(l)), E(l.get("create_time_str") or "")))
    return ('<div class="tbl-wrap"><table class="tlog"><thead><tr>'
            '<th>#</th><th>点赞人（备注（微信名））</th><th>点赞时间</th>'
            '</tr></thead><tbody>%s</tbody></table></div>' % "".join(rows))

def comment_rows_html(p):
    if not p["comments"]:
        return '<div class="none">（无评论）</div>'
    rows = []
    for i, c in enumerate(p["comments"], 1):
        ref = ""
        if c.get("ref_comment"):
            r = c["ref_comment"]
            ref = '<span class="tiny">↪ 回复 %s：%s</span>' % (E(r.get("display_name") or r.get("nickname") or ""),
                                                             E((r.get("content") or "")[:100]))
        content = c.get("content")
        if not content and (c.get("imagelist") or []):
            content = "[图片评论]"
        elif not content and (c.get("emojilist") or []):
            content = "[表情评论]"
        elif not content:
            content = "（空）"
        ctype = c.get("comment_content_type_name") or ""
        imgs = ""
        for m in [x for x in (p.get("comment_media_files") or []) if str(x.get("comment_id")) == str(c.get("comment_id"))]:
            if m.get("status") == "ok":
                f = rel(m["file"])
                imgs += '<a href="%s" target="_blank"><img class="cimg" src="%s" loading="lazy" alt="评论附图"></a>' % (f, f)
        rows.append('<tr><td>%d</td><td class="nm">%s</td><td>%s</td><td>%s%s%s</td><td>%s</td></tr>'
                    % (i, E(person(c)), E(c.get("create_time_str") or ""), E(content),
                       ("<br>" + ref) if ref else "", imgs, E(ctype)))
    return ('<div class="tbl-wrap"><table class="tlog"><thead><tr>'
            '<th>#</th><th>评论人（备注（微信名））</th><th>评论时间</th><th>评论内容 / 回复关系</th><th>类型</th>'
            '</tr></thead><tbody>%s</tbody></table></div>' % "".join(rows))

def media_html(p):
    out = []
    for m in p.get("media_files") or []:
        if m.get("media_kind") == "video":
            poster = ""
            if (m.get("thumb") or {}).get("file") and (m.get("thumb") or {}).get("status") == "ok":
                poster = ' poster="%s"' % rel(m["thumb"]["file"])
            if m.get("status") == "ok":
                dur = m.get("video_duration") or ""
                dur = (str(dur)[:6] + "s") if dur else ""
                out.append('<figure class="ph vid"><video controls preload="none"%s>'
                           '<source src="%s" type="video/mp4"></video>'
                           '<figcaption>视频 P%s · %s · %s</figcaption></figure>'
                           % (poster, rel(m["file"]), m["media_index"], E(dur), fmt_size(m.get("decrypted_bytes"))))
            else:
                out.append('<div class="missing">⚠ 视频 #%s 未能导出：%s</div>' % (m.get("media_index"), E(str(m.get("recovery_note") or m.get("error") or "未知原因"))))
        else:
            if m.get("status") == "ok":
                f = rel(m["file"])
                out.append('<figure class="ph"><a href="%s" target="_blank"><img src="%s" loading="lazy" alt="图%s"></a>'
                           '<figcaption>P%s · %s · %s×%s</figcaption></figure>'
                           % (f, f, m["media_index"], m["media_index"], E(m.get("format", "")),
                              E(str(m.get("width_xml") or "?")), E(str(m.get("height_xml") or "?"))))
            elif m.get("status") == "external":
                out.append('<div class="missing" style="color:#8b949e">🔗 媒体 #%s：外部链接媒体（音乐/链接封面等，非朋友圈 CDN），未下载</div>' % m.get("media_index"))
            else:
                out.append('<div class="missing">⚠ 图片 #%s 未能导出：%s</div>' % (m.get("media_index"), E(str(m.get("recovery_note") or m.get("error") or "未知原因"))))
    return "".join(out) or '<div class="none">（无图片/视频）</div>'

def loc_html(p):
    loc = p.get("location") or {}
    poi = (loc.get("poiName") or loc.get("city") or "").strip()
    addr = (loc.get("poiAddress") or "").strip()
    try:
        lat, lon = float(loc.get("latitude") or 0), float(loc.get("longitude") or 0)
    except (TypeError, ValueError):
        lat, lon = 0.0, 0.0
    if not poi and not addr and not (lat and lon):
        return '<span class="none">无位置</span>'
    parts = []
    if poi:
        parts.append('<b>%s</b>' % E(poi))
    if addr:
        parts.append(E(addr))
    out = " · ".join(parts)
    if lat and lon:
        out += ' · <a href="https://uri.amap.com/marker?position=%s,%s&name=%s" target="_blank">地图</a>' % (
            lon, lat, E((poi or addr or "").replace(" ", "")))
    return out

def loc_text(p):
    loc = p.get("location") or {}
    poi = (loc.get("poiName") or loc.get("city") or "").strip()
    addr = (loc.get("poiAddress") or "").strip()
    if not poi and not addr:
        return ""
    return '<b>%s</b>' % E(poi) if poi else E(addr)

def flags_html(p):
    labels = [("private", "私密"), ("isTop", "置顶"), ("sightFolded", "折叠"), ("showFlag", "showFlag")]
    parts = ['<span class="flag">%s</span>' % E(p.get("content_object_type_name") or "")]
    if p.get("archived"):
        parts.append('<span class="flag" style="color:#d29922;border-color:#d29922">历史归档 · 最新快照未包含</span>')
    if p.get("media_failed"):
        parts.append('<span class="flag" style="color:#f85149;border-color:#f85149">媒体缺失 %d</span>' % p["media_failed"])
    for k, lab in labels:
        v = p.get(k)
        if v not in (None, "", "0"):
            parts.append('<span class="flag">%s=%s</span>' % (lab, E(str(v))))
    return " ".join(parts)

def raw_html(p):
    loc = ""
    if p.get("location"):
        loc = '<div class="kv"><b>location 原始属性</b>: <code>%s</code></div>' % E(json.dumps(p["location"].get("raw_attrs"), ensure_ascii=False))
    return ('<details class="raw"><summary>原始字段 / 原始 XML</summary>'
            '<div class="kv"><b>tid(无符号)</b>: <code>%s</code> · <b>tid_db(有符号)</b>: <code>%s</code></div>'
            '<div class="kv"><b>createTime(epoch)</b>: <code>%s</code></div>%s'
            '<details><summary>查看原始 XML</summary><pre class="xml">%s</pre></details></details>'
            % (E(str(p.get("tid"))), E(str(p.get("tid_db"))), E(str(p.get("create_time"))),
               loc, E(p.get("raw_xml") or "")))

def card_html(p, open_card=False):
    idx = p["index"]
    likes, comments = p["likes"], p["comments"]
    return ('<details class="card" id="post-%d"%s><summary class="card-sum">'
            '<span class="idx">%03d</span><span class="date">%s</span><span class="wd">%s</span>'
            '<span class="stat">图 %d · 赞 %d · 评 %d</span><span class="loc">%s</span></summary>'
            '<div class="card-body"><div class="content">%s</div>'
            '<div class="meta-line">%s %s</div><div class="grid">%s</div>'
            '<h4>点赞（%d 人）</h4>%s<h4>评论（%d 条）</h4>%s%s</div></details>'
            % (idx, " open" if open_card else "", idx, E(p.get("create_time_str") or ""), E(p.get("weekday") or ""),
               p.get("media_count") or 0, len(likes), len(comments), loc_text(p),
               E(p.get("content") or "").replace("\n", "<br>"), flags_html(p), loc_html(p),
               media_html(p), len(likes), like_rows_html(p), len(comments), comment_rows_html(p), raw_html(p)))

CSS = """
:root{--bg:#0d1117;--card:#161b22;--card2:#1c2230;--fg:#e6edf3;--dim:#8b949e;--accent:#58a6ff;--green:#3fb950;--pink:#f778ba;--border:#30363d;--chip:#21262d}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.6 -apple-system,"PingFang SC","Helvetica Neue",Arial,sans-serif;padding:0 0 80px}
header.top{background:linear-gradient(135deg,#0d1117,#1a2740 60%,#2d1b3d);padding:34px 24px 26px;border-bottom:1px solid var(--border)}
h1{margin:0 0 6px;font-size:26px;letter-spacing:.5px}
.sub{color:var(--dim);font-size:13px}
.stats{display:flex;flex-wrap:wrap;gap:12px;margin-top:18px}
.stat-box{background:rgba(255,255,255,.05);border:1px solid var(--border);border-radius:10px;padding:10px 16px;min-width:110px}
.stat-box b{display:block;font-size:22px;color:var(--accent)}
.stat-box span{color:var(--dim);font-size:12px}
.wrap{max-width:1180px;margin:0 auto;padding:0 18px}
.toc{background:var(--card);border:1px solid var(--border);border-radius:12px;margin:22px 0;padding:14px 18px}
.toc a{color:var(--accent);text-decoration:none;margin-right:14px;font-size:13px}
.toc table{border-collapse:collapse;width:100%;margin-top:10px;font-size:13px}
.toc td,.toc th{border-bottom:1px solid var(--border);padding:6px 8px;text-align:left}
.toc th{color:var(--dim);font-weight:600}
.toc tr:hover td{background:rgba(88,166,255,.06)}
h2.yearhead{margin:28px 0 6px;font-size:16px;color:var(--dim);border-left:3px solid var(--accent);padding-left:10px}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;margin:14px 0;overflow:hidden}
.card[open]{box-shadow:0 4px 24px rgba(0,0,0,.4)}
summary.card-sum{cursor:pointer;list-style:none;display:flex;flex-wrap:wrap;align-items:baseline;gap:10px;padding:12px 16px;background:var(--card2);border-bottom:1px solid transparent}
.card[open] summary.card-sum{border-bottom-color:var(--border)}
summary.card-sum::-webkit-details-marker{display:none}
.idx{font-family:ui-monospace,Menlo,monospace;color:var(--dim);font-size:12px}
.date{font-weight:700;font-size:15px;color:#fff}
.wd{color:var(--dim)}
.stat{color:var(--green);font-size:12px;background:rgba(63,185,80,.1);border-radius:20px;padding:2px 10px}
.loc{color:var(--dim);font-size:12px;margin-left:auto}
.card-body{padding:14px 16px 18px}
.content{font-size:15px;white-space:normal;margin-bottom:10px}
.meta-line{color:var(--dim);font-size:12px;margin-bottom:12px}
.flag{display:inline-block;background:var(--chip);border:1px solid var(--border);border-radius:20px;padding:1px 9px;margin-right:6px;font-size:11px;color:var(--dim)}
.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:12px 0 6px}
figure.ph{margin:0;background:#000;border-radius:8px;overflow:hidden;border:1px solid var(--border)}
figure.ph img{display:block;width:100%;height:auto;max-height:none;object-fit:contain;background:#000}
figure.ph.vid video{display:block;width:100%;max-height:280px;background:#000}
figcaption{font-size:11px;color:var(--dim);padding:4px 6px;background:var(--card2)}
h4{margin:18px 0 8px;font-size:13px;color:var(--pink);font-weight:600;letter-spacing:.3px}
.nm{color:#fff}
.tbl-wrap{max-height:none;overflow:visible;border:1px solid var(--border);border-radius:8px}
table.tlog{border-collapse:collapse;width:100%;font-size:12.5px}
table.tlog th{position:sticky;top:0;background:#21262d;color:var(--dim);text-align:left;padding:7px 9px;font-weight:600;border-bottom:1px solid var(--border);white-space:nowrap}
table.tlog td{padding:6px 9px;border-bottom:1px solid rgba(48,54,61,.6);vertical-align:top}
table.tlog tr:nth-child(even) td{background:rgba(255,255,255,.02)}
table.tlog tr:hover td{background:rgba(88,166,255,.07)}
.cimg{max-width:120px;max-height:120px;border-radius:6px;margin:4px 6px 0 0;border:1px solid var(--border)}
.none{color:var(--dim);font-style:italic;font-size:12px}
.tiny{color:var(--dim);font-size:11px}
details.raw{margin-top:16px;border-top:1px dashed var(--border);padding-top:10px}
details.raw summary{cursor:pointer;color:var(--accent);font-size:12px}
.kv{font-size:11.5px;color:var(--dim);margin:4px 0;word-break:break-all}
pre.xml{background:#0b0f14;border:1px solid var(--border);border-radius:8px;padding:10px;font-size:11px;overflow:auto;max-height:420px;white-space:pre-wrap;word-break:break-all;color:#a5b3c2}
.missing{color:#f85149;font-size:12px;padding:6px}
button.tool{background:var(--chip);color:var(--fg);border:1px solid var(--border);border-radius:8px;padding:5px 12px;font-size:12px;cursor:pointer;margin-right:8px}
button.tool:hover{border-color:var(--accent);color:var(--accent)}
footer{color:var(--dim);font-size:12px;text-align:center;margin-top:40px}
a{color:var(--accent)}
@media (max-width:600px){.loc{margin-left:0;width:100%}summary.card-sum{gap:6px}}
.side{position:fixed;left:0;top:0;bottom:0;width:272px;background:#0a0f16;border-right:1px solid var(--border);display:flex;flex-direction:column;z-index:50;transition:transform .18s ease}
.side-head{padding:14px 14px 6px;font-size:15px;color:var(--fg);display:flex;align-items:baseline;gap:8px}
.side .filt{margin:6px 12px 8px;background:#11161d;border:1px solid var(--border);color:var(--fg);border-radius:8px;padding:6px 9px;font-size:12px;outline:none}
.side .filt:focus{border-color:var(--accent)}
.side-scroll{overflow:auto;flex:1;padding:0 8px 70px}
.side-year{margin:3px 0}
.side-year>summary{cursor:pointer;color:var(--green);font-size:12.5px;padding:3px 4px;border-radius:6px}
.side-year>summary:hover{background:rgba(63,185,80,.08)}
.side-list a{display:block;color:var(--fg);text-decoration:none;font-size:12px;line-height:1.45;padding:3px 6px;border-radius:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.side-list a:hover{background:rgba(88,166,255,.12)}
.side-list a.active{background:rgba(88,166,255,.2);color:#79c0ff}
.side .sd{color:var(--dim);font-family:ui-monospace,Menlo,monospace;margin-right:7px}
.main{margin-left:272px}
.side-toggle{position:fixed;left:14px;bottom:14px;z-index:80;box-shadow:0 4px 16px rgba(0,0,0,.45)}
body.side-collapsed .side{transform:translateX(-100%)}
body.side-collapsed .main{margin-left:0}
.card{scroll-margin-top:12px}
@media (max-width:1100px){.side{transform:translateX(-100%);box-shadow:0 0 40px rgba(0,0,0,.6)}.main{margin-left:0}body.side-open .side{transform:none}}
@media (max-width:900px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (max-width:620px){.grid{grid-template-columns:repeat(1,minmax(0,1fr))}}
"""

JS = """
(function(){
  var cards = document.querySelectorAll('details.card');
  var ea = document.getElementById('expandAll'), ca = document.getElementById('collapseAll');
  if(ea) ea.addEventListener('click', function(){ cards.forEach(function(c){c.open=true;}); });
  if(ca) ca.addEventListener('click', function(){ cards.forEach(function(c){c.open=false;}); });
  var tg = document.getElementById('sideToggle');
  if(tg) tg.addEventListener('click', function(){
    var narrow = window.matchMedia('(max-width:1100px)').matches;
    if(narrow){ document.body.classList.toggle('side-open'); }
    else { document.body.classList.toggle('side-collapsed'); }
  });
  var fi = document.getElementById('sideFilter');
  if(fi) fi.addEventListener('input', function(){
    var q = this.value.trim().toLowerCase();
    document.querySelectorAll('.side-list a').forEach(function(a){
      var t = (a.getAttribute('data-text') || '') + ' ' + a.textContent;
      a.style.display = (!q || t.toLowerCase().indexOf(q) >= 0) ? '' : 'none';
    });
    if(q){ document.querySelectorAll('.side-year').forEach(function(d){ d.open = true; }); }
  });
  if('IntersectionObserver' in window){
    var links = {};
    document.querySelectorAll('.side-list a').forEach(function(a){ links[a.getAttribute('href').slice(1)] = a; });
    var io = new IntersectionObserver(function(es){
      es.forEach(function(e){
        if(e.isIntersecting){
          var a = links[e.target.id];
          if(a){
            document.querySelectorAll('.side-list a.active').forEach(function(x){ x.classList.remove('active'); });
            a.classList.add('active');
          }
        }
      });
    }, {rootMargin: '-45% 0px -50% 0px'});
    cards.forEach(function(c){ io.observe(c); });
  }
})();
"""

def sidebar_html():
    years = sorted({p["create_time_str"][:4] for p in posts}, reverse=True)
    newest = years[0] if years else ""
    groups = []
    for y in years:
        yposts = [p for p in posts if p["create_time_str"][:4] == y]
        links = []
        for p in sorted(yposts, key=lambda x: x["create_time"], reverse=True):
            txt = " ".join((p.get("content") or "").replace("\u200b", " ").split())
            label = txt[:18] if txt else "（无正文）"
            links.append('<a href="#post-%d" data-text="%s"><span class="sd">%s</span>%s</a>'
                         % (p["index"], E(txt[:60]), p["create_time_str"][5:10], E(label)))
        groups.append('<details class="side-year"%s><summary>%s 年 · %d 条</summary><div class="side-list">%s</div></details>'
                      % (" open" if y == newest else "", y, len(yposts), "".join(links)))
    return ('<nav class="side" id="side"><div class="side-head"><b>目录</b><span class="tiny">共 %d 条 · 点击跳转</span></div>'
            '<input class="filt" id="sideFilter" placeholder="筛选帖子（正文/日期）" autocomplete="off">'
            '<div class="side-scroll">%s</div></nav>'
            '<button class="tool side-toggle" id="sideToggle">☰ 目录</button>'
            % (len(posts), "".join(groups)))

def build_html():
    year_stats = defaultdict(lambda: {"posts": 0, "images": 0, "videos": 0, "likes": 0, "comments": 0})
    for p in posts:
        y = p["create_time_str"][:4]
        st = year_stats[y]
        st["posts"] += 1
        st["likes"] += len(p["likes"])
        st["comments"] += len(p["comments"])
        st["images"] += sum(1 for m in (p.get("media_files") or []) if m.get("media_kind") == "image")
        st["videos"] += sum(1 for m in (p.get("media_files") or []) if m.get("media_kind") == "video")
    total_bytes = sum(m.get("decrypted_bytes") or 0 for m in d.get("media", []) if m.get("status") == "ok")
    toc_rows = []
    for y in sorted(year_stats, reverse=True):
        st = year_stats[y]
        yposts = [p for p in posts if p["create_time_str"][:4] == y]
        first, last = min(yposts, key=lambda p: p["create_time"]), max(yposts, key=lambda p: p["create_time"])
        toc_rows.append('<tr><td><a href="#year-%s">%s</a></td><td>%d</td><td>%d</td><td>%d</td><td>%d</td><td>%d</td>'
                        '<td class="tiny">%s ~ %s <a href="#post-%d">最新</a></td></tr>'
                        % (y, y, st["posts"], st["images"], st["videos"], st["likes"], st["comments"],
                           E(first["create_time_str"][:10]), E(last["create_time_str"][:10]), last["index"]))
    toc = ('<div class="toc"><b>年度概览</b> <span class="tiny">（点击年份快速跳转；共 %d 条朋友圈）</span>'
           '<table><tr><th>年份</th><th>帖子</th><th>图片</th><th>视频</th><th>点赞</th><th>评论</th><th>时间范围</th></tr>%s</table></div>'
           % (len(posts), "".join(toc_rows)))
    body_cards = []
    cur_year, open_first = None, True
    for p in sorted(posts, key=lambda x: x["create_time"], reverse=True):
        y = p["create_time_str"][:4]
        if y != cur_year:
            body_cards.append('<h2 class="yearhead" id="year-%s">%s 年</h2>' % (y, y))
            cur_year = y
        body_cards.append(card_html(p, open_card=True))
    fail_n = sum(1 for m in d.get("media", []) if m.get("status") not in ("ok", "external"))
    ext_n = sum(1 for m in d.get("media", []) if m.get("status") == "external")
    ext_notice = ""
    if ext_n:
        ext_notice = ('<div class="toc" style="border-color:#8b949e"><b style="color:#8b949e">外部链接媒体说明</b>'
                      '<div class="tiny" style="margin-top:6px">另有 <b>%d</b> 个媒体为外部链接（音乐/链接封面等，非朋友圈 CDN），'
                      '未下载；其链接地址已保留在 JSON/原始 XML 中，卡片内以 🔗 标注。</div></div>' % ext_n)
    notice = ""
    if fail_n:
        ghost = meta.get("ghost_posts") or []
        ghost_txt = ("涉及帖子 " + " / ".join("%03d" % g for g in ghost) + "（卡片内已用红色标注）。") if ghost else ""
        notice = ('<div class="toc" style="border-color:#f85149"><b style="color:#f85149">数据完整性说明</b>'
                  '<div class="tiny" style="margin-top:6px">共 <b style="color:#f85149">%d</b> 个媒体文件无法导出：微信服务器已清理这些朋友圈的媒体（CDN 返回 404），本机也无缓存副本。'
                  '%s其余 <b>%d</b> 个媒体均已完整解密导出。</div></div>'
                  % (fail_n, ghost_txt, meta["media_downloaded"]))
    inc_notice = ""
    if meta.get("incremental"):
        first_up = meta.get("first_exported_at") or ""
        last_up = meta.get("last_updated_at") or ""
        runs_n = meta.get("run_count") or 0
        arch_n = meta.get("archived_post_count") or 0
        vis_n = meta.get("visible_post_count")
        vis_txt = ("（其中当前可见 <b>%d</b> 条）" % vis_n) if vis_n is not None else ""
        inc_notice = ('<div class="toc" style="border-color:#3fb950"><b style="color:#3fb950">增量同步说明</b>'
                      '<div class="tiny" style="margin-top:6px">每次同步自动合并全部历史快照与旧归档，<b>只增不删</b>：'
                      '已累计同步 <b>%d</b> 次（首次 %s，最近更新 %s），共保留 <b>%d</b> 条帖子%s；'
                      '其中 <b>%d</b> 条未出现在最新微信快照中（可能因朋友圈可读期限、客户端缓存清理或已删除），'
                      '仍完整保留在归档中，媒体文件也已保留（卡片标注“历史归档 · 最新快照未包含”）。'
                      '历史媒体文件只复用、不覆盖。</div></div>'
                      % (runs_n, E(first_up), E(last_up), meta["post_count"], vis_txt, arch_n))
        if meta.get("since"):
            inc_notice = inc_notice.replace(
                "历史媒体文件只复用、不覆盖。</div></div>",
                "历史媒体文件只复用、不覆盖。增量导入起点 <b>%s</b>（更早历史不追溯，已归档内容保留）。</div></div>"
                % E(meta["since"]))
    acct = meta.get("header_account") or ("账号：%s（%s）" % (meta.get("account_nickname") or "", meta.get("account_wxid") or ""))
    body = (sidebar_html() + '<div class="main"><header class="top"><div class="wrap"><h1>微信朋友圈全量导出</h1>'
            '<div class="sub">%s · 时间范围：%s ~ %s（UTC+8） · 生成时间：%s</div>'
            '<div class="stats">'
            '<div class="stat-box"><b>%d</b><span>条朋友圈</span></div>'
            '<div class="stat-box"><b>%d</b><span>历史归档保留</span></div>'
            '<div class="stat-box"><b>%d</b><span>张图片</span></div>'
            '<div class="stat-box"><b>%d</b><span>个视频</span></div>'
            '<div class="stat-box"><b>%d</b><span>个点赞</span></div>'
            '<div class="stat-box"><b>%d</b><span>条评论</span></div>'
            '<div class="stat-box"><b>%d</b><span>张评论附图</span></div>'
            '<div class="stat-box"><b>%s</b><span>媒体总大小</span></div>'
            '</div></div></header><div class="wrap">'
            '<div style="margin:18px 0"><button class="tool" id="expandAll">全部展开</button>'
            '<button class="tool" id="collapseAll">全部收起</button>'
            '<span class="tiny">照片每行 3 张 · 点赞 / 评论已全部展开（表格不再内嵌滚动）· 显示格式为“备注（微信名）”，无备注时仅显示微信名。</span></div>'
            '%s%s%s%s%s<footer>由本机微信数据库解密导出 · 数据含隐私，请勿外传</footer></div></div>'
            % (E(acct),
               E(meta["range"]["start"]), E(meta["range"]["end"]), E(meta.get("generated_at_str") or ""),
               meta["post_count"], meta.get("archived_post_count") or 0,
               meta["image_count"], meta["video_count"], meta["like_count"],
               meta["comment_count"], meta["comment_media_count"], fmt_size(total_bytes),
               inc_notice, notice, ext_notice, toc, "".join(body_cards)))
    body = body.replace("微信朋友圈全量导出", E(TITLE))
    doc = ('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
           '<meta name="viewport" content="width=device-width,initial-scale=1">'
           '<title>微信朋友圈全量导出 · %s</title><style>%s</style></head><body>%s<script>%s</script></body></html>'
           % (E(meta.get("account_nickname") or ""), CSS, body, JS))
    doc = doc.replace("<title>微信朋友圈全量导出", "<title>" + E(TITLE))
    path = os.path.join(REPORT, HTML_NAME)
    open(path, "w", encoding="utf-8").write(doc)
    print("html:", path, os.path.getsize(path) // 1024, "KB")

def build_md():
    lines = ["# " + TITLE, "",
             "- %s" % (meta.get("header_account") or ("账号：%s（%s）" % (meta.get("account_nickname"), meta.get("account_wxid")))),
             "- 时间范围：%s ~ %s（UTC+8）" % (meta["range"]["start"], meta["range"]["end"]),
             "- 条数：%d 条朋友圈 · %d 张图片 · %d 个视频 · %d 个点赞 · %d 条评论 · %d 张评论附图"
             % (meta["post_count"], meta["image_count"], meta["video_count"], meta["like_count"],
                meta["comment_count"], meta["comment_media_count"]),
             "- 点赞/评论显示格式：备注（微信名）", ""]
    if meta.get("incremental"):
        lines.append("- 增量归档：共保留 %d 条（其中 %d 条已从微信不可见但保留在归档中）；累计同步 %d 次，首次 %s，最近更新 %s" % (
            meta.get("post_count") or 0, meta.get("archived_post_count") or 0, meta.get("run_count") or 0,
            meta.get("first_exported_at") or "", meta.get("last_updated_at") or ""))
        if meta.get("since"):
            lines.append("- 增量导入起点：%s 起（更早历史不追溯，已归档内容保留）" % meta["since"])
        lines.append("")
    for p in sorted(posts, key=lambda x: x["create_time"], reverse=True):
        tag = " · 历史归档（最新快照未包含）" if p.get("archived") else ""
        lines.append("## [%03d] %s %s%s" % (p["index"], p["create_time_str"], p.get("weekday") or "", tag))
        lines.append("")
        lines.append((p.get("content") or "").strip() or "（无正文）")
        lines.append("")
        loc = p.get("location")
        if loc:
            lines.append("> 位置：%s %s（%s, %s）" % (loc.get("poiName") or "", loc.get("poiAddress") or "",
                                                    loc.get("latitude"), loc.get("longitude")))
            lines.append("")
        lines.append("- 图片 %d 张 / 视频 %d 个" % (
            sum(1 for m in p.get("media_files") or [] if m.get("media_kind") == "image"),
            sum(1 for m in p.get("media_files") or [] if m.get("media_kind") == "video")))
        if p["likes"]:
            lines.append("")
            lines.append("**点赞（%d）**" % len(p["likes"]))
            lines.append("")
            lines.append("| # | 点赞人（备注（微信名）） | 时间 |")
            lines.append("|---|---|---|")
            for i, l in enumerate(p["likes"], 1):
                lines.append("| %d | %s | %s |" % (i, person(l).replace("|", "/"), l.get("create_time_str") or ""))
        if p["comments"]:
            lines.append("")
            lines.append("**评论（%d）**" % len(p["comments"]))
            lines.append("")
            lines.append("| # | 评论人（备注（微信名）） | 时间 | 内容 |")
            lines.append("|---|---|---|---|")
            for i, c in enumerate(p["comments"], 1):
                txt = c.get("content") or ""
                if not txt and c.get("imagelist"):
                    txt = "[图片评论]"
                if c.get("ref_comment"):
                    txt += " ↪回复 %s" % (c["ref_comment"].get("display_name") or "")
                lines.append("| %d | %s | %s | %s |" % (i, person(c).replace("|", "/"),
                                                       c.get("create_time_str") or "",
                                                       txt.replace("|", "/").replace("\n", " ")))
        lines.append("")
    path = os.path.join(REPORT, MD_NAME)
    open(path, "w", encoding="utf-8").write("\n".join(lines))
    print("md:", path, os.path.getsize(path) // 1024, "KB")

def wcsv(path, header, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(r)
    print("csv:", path, len(rows), "rows")

def build_csv():
    post_rows, like_rows, com_rows, media_rows, cmedia_rows = [], [], [], [], []
    for p in sorted(posts, key=lambda x: x["create_time"], reverse=True):
        loc = p.get("location") or {}
        post_rows.append([
            p["index"], p["tid"], p.get("tid_db"), p["create_time_str"], p.get("weekday") or "",
            (p.get("content") or "").replace("\r\n", "\n"),
            sum(1 for m in p.get("media_files") or [] if m.get("media_kind") == "image"),
            sum(1 for m in p.get("media_files") or [] if m.get("media_kind") == "video"),
            len(p["likes"]), len(p["comments"]),
            p.get("content_object_type_name") or "", loc.get("city") or "",
            loc.get("poiName") or "", loc.get("poiAddress") or "",
            loc.get("latitude") or "", loc.get("longitude") or "",
            "是" if str(p.get("private")) == "1" else "否",
            "是" if str(p.get("isTop")) == "1" else "否", p.get("raw_xml_file") or ""])
        for i, l in enumerate(p["likes"], 1):
            like_rows.append([p["index"], p["create_time_str"], (p.get("content") or "")[:40],
                              i, l.get("remark") or "", l.get("wechat_nickname") or "",
                              person(l), l.get("notice_nickname") or "",
                              "是" if l.get("in_contacts") else "否",
                              l.get("create_time_str") or "", l.get("username") or ""])
        for i, c in enumerate(p["comments"], 1):
            txt = c.get("content") or ""
            r = c.get("ref_comment") or {}
            com_rows.append([p["index"], p["create_time_str"], (p.get("content") or "")[:40],
                             i, c.get("comment_id") or "", c.get("remark") or "",
                             c.get("wechat_nickname") or "", person(c),
                             c.get("notice_nickname") or "", txt.replace("\r\n", "\n"),
                             c.get("create_time_str") or "", c.get("ref_comment_id") or "",
                             r.get("display_name") or "", (r.get("content") or "")[:200],
                             c.get("comment_content_type") or "",
                             c.get("comment_imageinfo_count") or "", c.get("comment_emojiinfo_count") or "",
                             c.get("username") or ""])
        for m in p.get("media_files") or []:
            media_rows.append([p["index"], p["create_time_str"], m.get("media_index"), m.get("media_kind"),
                               os.path.basename(m.get("file") or ""), m.get("decrypted_bytes") or "",
                               m.get("format") or "", m.get("width_xml") or "", m.get("height_xml") or "",
                               m.get("total_size_xml") or "", m.get("md5_xml") or "", m.get("key") or "",
                               m.get("video_duration") or "", m.get("status") or "", m.get("error") or "",
                               m.get("file") or ""])
        for m in (p.get("comment_media_files") or []):
            cmedia_rows.append([p["index"], p["create_time_str"], m.get("comment_id"),
                                m.get("comment_media_index"), os.path.basename(m.get("file") or ""),
                                m.get("status") or "", m.get("error") or "", m.get("md5_xml") or "",
                                m.get("width_xml") or "", m.get("height_xml") or "", m.get("file") or ""])
    wcsv(os.path.join(ALLD, "posts.csv"),
         ["编号", "tid(无符号)", "tid_db(有符号)", "发布时间", "星期", "正文", "图片数", "视频数",
          "点赞数", "评论数", "内容类型", "城市", "地点POI", "地址", "纬度", "经度", "私密", "置顶", "原始XML文件"],
         post_rows)
    wcsv(os.path.join(ALLD, "likes.csv"),
         ["帖子编号", "帖子时间", "帖子正文摘要", "序号", "备注", "微信名", "显示名（备注（微信名））",
          "通知昵称", "在联系人库", "点赞时间", "username"], like_rows)
    wcsv(os.path.join(ALLD, "comments.csv"),
         ["帖子编号", "帖子时间", "帖子正文摘要", "序号", "comment_id", "备注", "微信名",
          "显示名（备注（微信名））", "通知昵称", "评论内容", "评论时间", "回复评论ID",
          "回复对象显示名", "被回复内容摘要", "评论内容类型", "图片数", "表情数", "username"], com_rows)
    wcsv(os.path.join(ALLD, "media.csv"),
         ["帖子编号", "帖子时间", "媒体序号", "类型", "文件名", "解密后字节", "格式", "宽", "高",
          "XML总大小", "md5", "key", "视频时长", "状态", "错误", "相对路径"], media_rows)
    wcsv(os.path.join(ALLD, "comment_media.csv"),
         ["帖子编号", "帖子时间", "评论ID", "评论图序号", "文件名", "状态", "错误", "md5",
          "宽", "高", "相对路径"], cmedia_rows)

def main():
    build_html()
    build_md()
    build_csv()
    print("ALL DONE")

if __name__ == "__main__":
    main()
