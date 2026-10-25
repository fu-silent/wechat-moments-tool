#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate README.md for a friend Moments deliverable from its JSON metadata."""
import argparse
import glob
import json
import os
import sys

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(SCRIPTS)
ROOT = os.path.dirname(PROJECT)
B = chr(96) * 3

sys.path.insert(0, SCRIPTS)
import wxconfig

def fmt(n):
    n = int(n or 0)
    if n >= 1048576:
        return "%.1f MB" % (n / 1048576.0)
    if n >= 1024:
        return "%.0f KB" % (n / 1024.0)
    return "%d B" % n

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--key", default="")
    a = ap.parse_args()
    DELIV = os.path.abspath(a.dir)
    cands = sorted(glob.glob(os.path.join(DELIV, "moments_*.json")))
    if not cands:
        raise SystemExit("no moments_*.json in " + DELIV)
    jp = cands[0]
    targets = wxconfig.targets()
    d = json.load(open(jp, encoding="utf-8"))
    meta = d["meta"]
    key = a.key
    if not key:
        for k, t in targets.items():
            if os.path.abspath(os.path.join(ROOT, t["outdir"])) == DELIV:
                key = k
    target = targets.get(key, {})
    name = target.get("name") or meta.get("target_remark") or meta.get("target_nickname") or ""
    posts = d.get("posts") or []
    media = d.get("media") or []
    cmedia = d.get("comment_media") or []
    ok = [m for m in media if m.get("status") == "ok"]
    ext = [m for m in media if m.get("status") == "external"]
    bad = [m for m in media if m.get("status") not in ("ok", "external")]
    ok_cm = [m for m in cmedia if m.get("status") == "ok"]
    bad_posts = sorted({m["post_index"] for m in bad})
    arch = [p for p in posts if p.get("archived")]
    vis = [p for p in posts if not p.get("archived")]
    disk_bytes = 0
    for sub in ("media", "raw_xmls"):
        base = os.path.join(DELIV, sub)
        for root, _, files in os.walk(base):
            for fn in files:
                disk_bytes += os.path.getsize(os.path.join(root, fn))
    zip_path = ""
    for z in sorted(glob.glob(os.path.join(ROOT, "wechat-moments-%s*.zip" % key))):
        zip_path = z
    L = []
    L.append("# 微信朋友圈导出 · %s" % name)
    L.append("")
    L.append("> 本目录是由本机微信数据库解密后导出的 **%s** 微信朋友圈归档，含全部可见文字、图片、视频、位置、点赞与评论。" % name)
    L.append("")
    L.append("## 导出对象")
    L.append("")
    L.append("| 项目 | 值 |")
    L.append("|---|---|")
    for k, v in [("备注", meta.get("target_remark")), ("微信昵称", meta.get("target_nickname")),
                 ("wxid", meta.get("target_wxid")), ("微信号(alias)", meta.get("target_alias")),
                 ("全拼", meta.get("target_remark_quan_pin"))]:
        if v:
            L.append("| %s | %s |" % (k, v))
    L.append("| 导出账号 | %s（%s） |" % (meta.get("account_nickname") or "", meta.get("account_wxid") or ""))
    L.append("")
    L.append("## 数量概览")
    L.append("")
    L.append("| 指标 | 数量 |")
    L.append("|---|---|")
    L.append("| 朋友圈帖子 | %d 条（其中历史归档 %d 条） |" % (len(posts), len(arch)))
    L.append("| 图片 | %d 张（已下载 %d） |" % (meta.get("image_count", 0), sum(1 for m in ok if m.get("media_kind") == "image")))
    L.append("| 视频 | %d 个（已下载 %d） |" % (meta.get("video_count", 0), sum(1 for m in ok if m.get("media_kind") == "video")))
    L.append("| 点赞 | %d 个 |" % meta.get("like_count", 0))
    L.append("| 评论 | %d 条 |" % meta.get("comment_count", 0))
    L.append("| 评论附图 | %d 张（已下载 %d） |" % (len(cmedia), len(ok_cm)))
    L.append("| 媒体总大小 | %s |" % fmt(disk_bytes))
    L.append("| 未能导出的媒体 | %d 个 |" % len(bad))
    if ext:
        L.append("| 外部链接媒体（音乐/链接封面等，非 CDN） | %d 个 |" % len(ext))
    L.append("")
    L.append("## 时间范围")
    L.append("")
    rng = meta.get("range") or {}
    L.append("- 累计归档范围（含已不在最新快照中的历史帖子）：%s ~ %s（UTC+8）" % (rng.get("start", ""), rng.get("end", "")))
    vr = meta.get("visible_range") or {}
    if vr:
        L.append("- 最新微信快照可见范围：%s ~ %s" % (vr.get("start", ""), vr.get("end", "")))
    if meta.get("incremental"):
        L.append("- 首次导出：%s；最近更新：%s；累计同步：%d 次" % (
            meta.get("first_exported_at", ""), meta.get("last_updated_at", ""), meta.get("run_count") or 0))
    if meta.get("since"):
        L.append("- 增量导入起点：**%s 起**（更早历史不再追溯；此前已归档的帖子仍保留，不删除）" % meta["since"])
    L.append("")
    L.append("## 文件清单")
    L.append("")
    html = meta.get("html_filename") or "moments.html"
    md = meta.get("md_filename") or "moments.md"
    xlsx = meta.get("xlsx_filename") or "导出.xlsx"
    json_name = os.path.basename(jp)
    L.append("| 文件 | 说明 |")
    L.append("|---|---|")
    L.append("| [%s](%s) | 主报告：左侧目录、按年分组、照片每行 3 张、点赞/评论全部展开（浏览器打开） |" % (html, html))
    L.append("| [%s](%s) | Markdown 版本 |" % (md, md))
    L.append("| [%s](%s) | 完整结构化数据（帖子/媒体/点赞/评论/原始 XML） |" % (json_name, json_name))
    L.append("| [%s](%s) | Excel 工作簿（8 个工作表） |" % (xlsx, xlsx))
    L.append("| [csv/](csv/) | posts / likes / comments / media / comment_media 五张表 |")
    L.append("| [media/](media/) | 解密后的图片 / 视频（按帖子分目录） |")
    L.append("| [raw_xmls/](raw_xmls/) | 每条朋友圈的原始 XML |")
    L.append("")
    L.append("## 显示规则")
    L.append("")
    L.append("- 点赞/评论中的好友显示为 **备注（微信昵称）**；没有备注时只显示微信昵称。")
    L.append("- 位置中的经纬度以 XML 原始属性为准（微信把 latitude/longitude 存反了，报告已按正确经纬度显示并生成地图链接）。")
    L.append("")
    L.append("## 数据完整性说明")
    L.append("")
    if bad:
        L.append("有 %d 个媒体文件无法导出（帖子 %s）：微信服务器已清理（CDN 返回 404/400）或下载时被拒，本机无缓存副本。报告中以红色“媒体缺失”标注。" % (
            len(bad), " / ".join("%03d" % i for i in bad_posts)))
    else:
        L.append("全部朋友圈媒体均已成功下载并解密。")
    if ext:
        L.append("")
        L.append("另有 %d 个外部链接媒体（如 QQ 音乐封面）不属于朋友圈 CDN，未下载，但链接与原始 XML 已保留。" % len(ext))
    if meta.get("finder_posts"):
        L.append("")
        L.append("帖子 %s 为视频号/链接类内容，微信未开放视频号媒体下载，仅保留元数据与封面。" % (
            " / ".join("%03d" % i for i in meta["finder_posts"])))
    L.append("")
    L.append("## 增量归档机制")
    L.append("")
    L.append("本目录采用**只增不删**的增量同步：每次同步会合并历史快照与旧归档，新帖子追加、已有帖子补充新的点赞/评论/媒体。"
             "朋友圈可能设置“可读期限”，旧帖在后续快照中消失时**不会被删除**，而是保留并标注“历史归档（最新快照未包含）”。"
             "历史媒体文件只复用、不覆盖；更新前会自动备份 JSON 到 project/data/state/。")
    L.append("")
    L.append("更新命令（会自动拍新快照并增量合并）：")
    L.append("")
    L.append(B + "bash")
    L.append("cd %s/project/scripts" % ROOT)
    L.append("python3 sync_friends.py --targets %s" % (key or "<target>"))
    L.append(B)
    L.append("")
    if zip_path:
        L.append("打包副本：%s（%s）" % (os.path.relpath(zip_path, ROOT), fmt(os.path.getsize(zip_path))))
        L.append("")
    L.append("---")
    L.append("")
    L.append("由本机微信数据库解密导出 · 数据含隐私，请勿外传。")
    out = os.path.join(DELIV, "README.md")
    open(out, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("README:", out, os.path.getsize(out), "bytes")

if __name__ == "__main__":
    main()
