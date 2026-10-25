#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Preflight check for the WeChat Moments export toolkit (read-only).

Checks, in order:
  1. Python version and third-party dependencies
  2. project/config.json exists, is valid and contains no placeholder values
  3. master key format (the key itself is never printed)
  4. resolved data paths / decrypted databases / snapshot dirs
  5. live WeChat sns dir discovery (only needed for new snapshots)
  6. configured friend targets

Then prints the next command(s) to run.

Usage:
  python3 doctor.py

Exit codes: 0 = ready, 1 = blocked (details above), 2 = config missing/invalid.
"""
import argparse
import hashlib
import json
import os
import stat
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wxconfig

BLOCKERS = []
WARNINGS = []
OKS = []


def ok(label, detail=""):
    OKS.append((label, detail))


def warn(label, detail=""):
    WARNINGS.append((label, detail))


def block(label, detail=""):
    BLOCKERS.append((label, detail))


def size_mb(n):
    return "%.1f MB" % (n / 1048576.0)


def main():
    ap = argparse.ArgumentParser(description="preflight check for the Moments export toolkit")
    ap.add_argument("--quiet", action="store_true", help="only print problems and next commands")
    a = ap.parse_args()

    # ---- 1. runtime ----
    py_ok = sys.version_info >= (3, 10)
    (ok if py_ok else block)("Python %d.%d.%d" % sys.version_info[:3],
                             "" if py_ok else "需要 Python >= 3.10")
    try:
        import Crypto
        ok("pycryptodome", getattr(Crypto, "__version__", "?"))
    except Exception as e:  # noqa: BLE001
        block("pycryptodome", "导入失败：%r（pip install -r project/requirements.txt）" % (e,))
    try:
        import openpyxl
        ok("openpyxl", getattr(openpyxl, "__version__", "?"))
    except Exception as e:  # noqa: BLE001
        block("openpyxl", "导入失败：%r（pip install -r project/requirements.txt）" % (e,))

    # ---- 2. config ----
    path = wxconfig.config_path()
    if not os.path.isfile(path):
        print("[doctor] 未找到配置文件：%s" % path)
        print("         请先执行：cp %s %s" % (wxconfig.CONFIG_PATH_EXAMPLE, path))
        return 2
    try:
        wxconfig.load()
        ok("config.json", path)
    except SystemExit as e:
        print("[doctor] 配置有误：%s" % (e,))
        return 2

    mode = stat.S_IMODE(os.stat(path).st_mode)
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        warn("config.json 权限", "%o（建议 chmod 600，文件含主口令）" % mode)
    else:
        ok("config.json 权限", "%o" % mode)

    # ---- 3. master key (never printed) ----
    try:
        key = wxconfig.master_key_hex()
        fp = hashlib.sha256(bytes.fromhex(key)).hexdigest()[:12]
        ok("master key", "64 hex chars, sha256 fingerprint=%s" % fp)
    except SystemExit as e:
        block("master key", str(e))

    # ---- 4. paths / decrypted DBs ----
    paths = wxconfig.paths()
    for label, p in (
        ("raw_dir", paths["raw_dir"]),
        ("decrypted_dir", paths["decrypted_dir"]),
        ("snapshots_dir", paths["snapshots_dir"]),
        ("state_dir", paths["state_dir"]),
        ("logs_dir", paths["logs_dir"]),
    ):
        if os.path.isdir(p):
            ok(label, p)
        else:
            warn(label, "目录不存在：%s（首次导出时可自动创建；快照/状态目录会随流程生成）" % p)
    sns_plain = paths["sns_plain"]
    if os.path.isfile(sns_plain):
        try:
            import sqlite3
            con = sqlite3.connect("file:%s?mode=ro" % sns_plain, uri=True)
            n = con.execute("SELECT COUNT(*) FROM SnsTimeLine").fetchone()[0]
            integrity = con.execute("PRAGMA integrity_check(1)").fetchone()[0]
            con.close()
            ok("sns_plain.db", "%s, SnsTimeLine=%d, integrity=%s" % (size_mb(os.path.getsize(sns_plain)), n, integrity))
        except Exception as e:  # noqa: BLE001
            block("sns_plain.db", "%s（无法读取：%r）" % (sns_plain, e))
    else:
        block("sns_plain.db", "未找到：%s\n"
              "  首次使用请把加密的 sns.db/-wal 放进 raw_dir 后执行：python3 decrypt_sns.py\n"
              "  或拍一个实时快照：python3 snapshot_db.py" % sns_plain)
    contact = paths["contact_plain"]
    if os.path.isfile(contact):
        ok("contact_plain.db", "%s, %s" % (size_mb(os.path.getsize(contact)), contact))
    else:
        warn("contact_plain.db", "未找到：%s\n"
             "  备注名显示会退化为昵称；可执行：python3 decrypt_db.py --db <微信contact.db> --out %s"
             % (contact, contact))
    idx_path = os.path.join(paths["snapshots_dir"], "index.json")
    if os.path.isfile(idx_path):
        try:
            idx = json.load(open(idx_path, encoding="utf-8"))
            ok("快照", "%d 个（最新：%s）" % (len(idx), idx[-1].get("snapshot_id") if idx else "-"))
        except Exception as e:  # noqa: BLE001
            warn("快照索引", "解析失败：%r" % (e,))
    else:
        warn("快照", "尚无快照索引（首次 sync_friends.py 会创建）")

    # ---- 5. live WeChat dir (needed for snapshots only) ----
    try:
        src = wxconfig.resolve_sns_dir()
        ok("live WeChat sns", src)
    except SystemExit as e:
        warn("live WeChat sns", str(e))

    # ---- 6. targets / self export ----
    targets = wxconfig.targets()
    ok("targets", "%d 个：%s" % (len(targets), ", ".join(
        "%s(%s)" % (k, (v.get("name") or "?")) for k, v in targets.items())))
    for key, t in targets.items():
        outdir = t.get("outdir") or ""
        full = outdir if os.path.isabs(outdir) else os.path.join(wxconfig.ROOT, outdir)
        marker = "已存在" if os.path.isdir(full) else "尚未创建"
        status = ("%s -> %s（%s）" % (t.get("wxid"), full, marker))
        if not str(t.get("wxid", "")).startswith("wxid_"):
            warn("targets.%s" % key, "wxid 看起来不合法：%r" % (t.get("wxid"),))
        else:
            ok("targets.%s" % key, status)
    self_export = wxconfig.self_export()
    ok("self_export", "%s (%s)" % (self_export["outdir"], self_export["slug"]))
    if self_export.get("expected"):
        ok("self_export.expected", "%d 项历史统计将做严格校验" % len(self_export["expected"]))

    # ---- report ----
    if not a.quiet:
        for label, detail in OKS:
            print("  [OK]   %-18s %s" % (label, detail))
    for label, detail in WARNINGS:
        print("  [WARN] %-18s %s" % (label, detail))
    for label, detail in BLOCKERS:
        print("  [BLOCK] %-17s %s" % (label, detail))
    print()
    if BLOCKERS:
        print("[doctor] 还不能开始导出：请先解决上面 BLOCK 项。")
        return 1
    print("[doctor] 配置与数据路径检查通过。")
    if WARNINGS:
        print("         有 %d 个警告（不影响已有归档的增量导出）。" % len(WARNINGS))
    print("\n下一步建议：")
    print("  增量同步好友：  python3 sync_friends.py --no-snapshot      # 已有快照")
    print("                  python3 sync_friends.py                   # 先拍新快照")
    print("  重新导出本人：  python3 export_all.py")
    print("  重建本人报告：  python3 build_report_all.py && python3 build_xlsx_all.py")
    print("  校验本人成果：  python3 verify_deliverables.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
