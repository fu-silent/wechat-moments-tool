#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Central configuration loader for the WeChat Moments export toolkit.

All machine-specific and private values (database master key, WeChat account,
paths, friend targets, report titles) live in ONE file:

    <workspace>/project/config.json        # real values, chmod 600, NEVER share
    <workspace>/project/config.example.json  # placeholder template, shareable

The config file may also be placed elsewhere and selected with:

    WX_MOMENTS_CONFIG=/path/to/my-config.json python3 export_all.py

Design notes
------------
* This module must stay dependency-free (stdlib only) and must not import any
  other project module (it is imported very early by every script).
* `load()` exits with a readable message (SystemExit) when the config is
  missing or structurally invalid; use `load(required=False)` to probe.
* Paths in the config may be absolute, `~`-relative, contain env vars, or be
  relative to the workspace root.
* Secrets are never printed by this module. `master_key_hex()` only returns
  the value (or fails with a hint).
"""
import json
import os
import re
import stat
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPTS_DIR)
ROOT = os.path.dirname(PROJECT_DIR)

CONFIG_PATH_DEFAULT = os.path.join(PROJECT_DIR, "config.json")
CONFIG_PATH_EXAMPLE = os.path.join(PROJECT_DIR, "config.example.json")
ENV_CONFIG = "WX_MOMENTS_CONFIG"
SCHEMA_VERSION = 1

_MISSING = object()
_cache = {"path": None, "mtime": None, "data": None, "warned": False}

_PLACEHOLDER_HINTS = (
    "replace_me", "replace-with", "change_me", "change-me", "your_", "your-",
    "<", ">", "todo", "xxx", "填", "示例", "占位",
)


class ConfigError(SystemExit):
    """Readable CLI error for configuration problems (exit code 1)."""

    def __init__(self, message):
        super().__init__("[config] " + message)


def config_path():
    """Resolved config file path (env override or <project>/config.json)."""
    p = os.environ.get(ENV_CONFIG) or CONFIG_PATH_DEFAULT
    p = os.path.expanduser(os.path.expandvars(p))
    return p if os.path.isabs(p) else os.path.abspath(p)


def is_placeholder(value):
    if value is None:
        return True
    if isinstance(value, str):
        s = value.strip().lower()
        if not s:
            return True
        return any(h in s for h in _PLACEHOLDER_HINTS)
    return False


def load(required=True):
    """Load and cache config.json.

    required=False returns None instead of raising when the file is missing.
    """
    path = config_path()
    if not os.path.isfile(path):
        if not required:
            return None
        raise ConfigError(
            "未找到配置文件：%s\n"
            "请复制模板并填写自己的信息：\n"
            "    cp %s %s\n"
            "（config.json 含微信主口令，权限建议 600，切勿分享/提交；"
            "模板里没有真实密钥。）"
            % (path, CONFIG_PATH_EXAMPLE, path))
    try:
        st = os.stat(path)
        mtime = st.st_mtime_ns
    except OSError as e:  # pragma: no cover - race
        raise ConfigError("无法读取配置文件 %s：%r" % (path, e))
    if _cache["path"] == path and _cache["mtime"] == mtime and _cache["data"] is not None:
        return _cache["data"]
    try:
        with open(path, "rb") as f:
            raw = f.read()
        data = json.loads(raw.decode("utf-8-sig"))
    except Exception as e:
        raise ConfigError("配置文件不是合法 JSON：%s\n  %r" % (path, e))
    if not isinstance(data, dict):
        raise ConfigError("配置文件顶层必须是 JSON 对象：%s" % path)
    _validate_structure(data, path)
    _cache.update(path=path, mtime=mtime, data=data)
    _warn_permissions(path, st)
    return data


def _validate_structure(data, path):
    ver = data.get("schema_version", 1)
    if ver != SCHEMA_VERSION:
        raise ConfigError("配置文件 schema_version=%r，本工具只支持 %d：%s"
                          % (ver, SCHEMA_VERSION, path))
    for key in ("wechat", "paths", "self_export", "targets"):
        if key not in data:
            raise ConfigError("配置文件缺少字段 %r：%s" % (key, path))
    if not isinstance(data["wechat"], dict):
        raise ConfigError("配置字段 wechat 必须是对象：%s" % path)
    if not isinstance(data["paths"], dict):
        raise ConfigError("配置字段 paths 必须是对象：%s" % path)
    if not isinstance(data["self_export"], dict):
        raise ConfigError("配置字段 self_export 必须是对象：%s" % path)
    if not isinstance(data["targets"], dict):
        raise ConfigError("配置字段 targets 必须是对象：%s" % path)
    wxid = (data["wechat"].get("self_wxid") or "").strip()
    if not wxid:
        raise ConfigError("配置字段 wechat.self_wxid 不能为空：%s" % path)
    if is_placeholder(wxid) or not wxid.startswith("wxid_"):
        raise ConfigError("wechat.self_wxid 看起来仍是占位符或不是 wxid_ 开头：%r\n"
                          "请在 %s 中填写自己的微信 wxid。" % (wxid, path))
    for name, t in data["targets"].items():
        if name.startswith("_"):
            continue  # _readme / comments in the example template
        if not isinstance(t, dict):
            raise ConfigError("targets.%s 必须是对象：%s" % (name, path))
        for field in ("wxid", "outdir", "slug"):
            if not (t.get(field) or "").strip():
                raise ConfigError("targets.%s 缺少字段 %r：%s" % (name, field, path))


def _warn_permissions(path, st):
    if _cache["warned"]:
        return
    mode = stat.S_IMODE(st.st_mode)
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        _cache["warned"] = True
        sys.stderr.write(
            "[config] 警告：%s 权限为 %o，建议 chmod 600（文件含微信主口令）\n"
            % (path, mode))


# ---------------------------------------------------------------- accessors
def get(dotted, default=_MISSING):
    """Read a dotted config value, e.g. get('wechat.self_name')."""
    data = load()
    cur = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            if default is _MISSING:
                raise ConfigError("配置文件缺少字段 %r（%s）" % (dotted, config_path()))
            return default
        cur = cur[part]
    return cur


def require(dotted):
    val = get(dotted)
    if is_placeholder(val):
        raise ConfigError("配置字段 %r 仍是占位符或为空，请在 %s 中填写。"
                          % (dotted, config_path()))
    return val


def expand_path(value):
    """Expand ~ / env vars; relative paths resolve against the workspace root."""
    if not value:
        return ""
    p = os.path.expanduser(os.path.expandvars(str(value)))
    return p if os.path.isabs(p) else os.path.abspath(os.path.join(ROOT, p))


def config_path_value(path):
    """Make an absolute path workspace-relative when possible (for JSON meta)."""
    try:
        rel = os.path.relpath(os.path.abspath(path), ROOT)
    except (TypeError, ValueError):
        return str(path)
    return rel.replace(os.sep, "/")


def path_of(dotted, default=""):
    val = get(dotted, default)
    return expand_path(val) if val else ""


def project_path(*parts):
    return os.path.join(ROOT, *parts)


# ---------------------------------------------------------------- typed values
def master_key_hex():
    val = require("wechat.master_key_hex")
    s = str(val).strip()
    if len(s) != 64 or not re.fullmatch(r"[0-9a-fA-F]{64}", s):
        raise ConfigError(
            "wechat.master_key_hex 必须是 64 位十六进制字符串（当前长度 %d）。\n"
            "这是微信 4.x 数据库的 32 字节主口令（不是微信登录密码）。"
            "请在 %s 中填写。" % (len(s), config_path()))
    return s.lower()


def master_key_bytes():
    return bytes.fromhex(master_key_hex())


def self_wxid():
    return require("wechat.self_wxid").strip()


def self_name():
    return (get("wechat.self_name", "") or "").strip()


def targets():
    """Friend targets, ignoring _readme/comment keys from the template."""
    return {k: v for k, v in get("targets").items()
            if not k.startswith("_") and isinstance(v, dict)}


def target_list():
    return list(get("targets").items())


def workers(default=8):
    try:
        n = int(get("export.workers", default))
    except (TypeError, ValueError):
        n = default
    return n if n > 0 else default


def legacy_snapshot_label():
    return (get("export.legacy_snapshot_label", "legacy_snapshot") or "legacy_snapshot").strip()


def self_export():
    """Resolved self-export settings (paths are absolute)."""
    sec = get("self_export")
    outdir = expand_path(sec.get("outdir") or "deliverables")
    slug = (sec.get("slug") or "moments_all").strip()
    title = (sec.get("title") or "我的朋友圈全量导出").strip()
    return {
        "outdir": outdir,
        "slug": slug,
        "title": title,
        "xlsx": (sec.get("xlsx") or "微信朋友圈全量导出.xlsx").strip(),
        "json": os.path.join(outdir, slug + ".json"),
        "html": os.path.join(outdir, slug + ".html"),
        "md": os.path.join(outdir, slug + ".md"),
        "expected": sec.get("expected") or None,
    }


def paths():
    """Resolved data paths (all absolute)."""
    dec = path_of("paths.decrypted_dir", "project/data/decrypted")
    raw = path_of("paths.raw_dir", "project/data/raw")
    return {
        "project": PROJECT_DIR,
        "root": ROOT,
        "raw_dir": raw,
        "decrypted_dir": dec,
        "snapshots_dir": path_of("paths.snapshots_dir", "project/data/snapshots"),
        "state_dir": path_of("paths.state_dir", "project/data/state"),
        "logs_dir": path_of("paths.logs_dir", "project/logs"),
        "sns_plain": expand_path(get("paths.sns_plain", "") or os.path.join(dec, "sns_plain.db")),
        "contact_plain": expand_path(get("paths.contact_plain", "") or os.path.join(dec, "contact_plain.db")),
        "raw_sns_db": expand_path(get("paths.raw_sns_db", "") or os.path.join(raw, "sns.db")),
        "raw_contact_db": expand_path(get("paths.raw_contact_db", "")
                                      or os.path.join(raw, "contact", "contact.db")),
    }


def container_dir():
    val = get("wechat.container_dir", "") or os.path.expanduser(
        "~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files")
    return expand_path(val)


def contact_db_encrypted():
    """Path to the encrypted contact.db, if the user configured one."""
    val = get("wechat.contact_db_encrypted", "") or ""
    return expand_path(val) if val else ""


def resolve_sns_dir():
    """Return the live WeChat sns db directory (config override or auto-detect)."""
    import glob as _glob
    explicit = get("wechat.sns_dir", "") or ""
    if explicit:
        p = expand_path(explicit)
        if os.path.exists(os.path.join(p, "sns.db")):
            return p
        raise ConfigError("wechat.sns_dir 指向的目录下没有 sns.db：%s" % p)
    container = container_dir()
    wxid = self_wxid()
    patterns = [
        os.path.join(container, wxid + "*", "db_storage", "sns"),
        os.path.join(container, "*", "db_storage", "sns"),
    ]
    for pat in patterns:
        for d in sorted(_glob.glob(pat)):
            if os.path.exists(os.path.join(d, "sns.db")):
                return d
    raise ConfigError(
        "在 %s 下找不到微信 sns.db。\n"
        "请在 %s 中设置 wechat.sns_dir（指向包含 sns.db 的目录），"
        "或把 wechat.container_dir 改成你的 xwechat_files 路径。"
        % (container, config_path()))


def summary_mask_key():
    """Human readable summary; never returns the key (or any slice of it)."""
    import hashlib
    key = get("wechat.master_key_hex", "") or ""
    s = str(key).strip()
    if len(s) == 64:
        fp = hashlib.sha256(bytes.fromhex(s)).hexdigest()[:12]
        return "master_key: 64 hex chars, sha256 fingerprint=%s" % fp
    return "master_key: missing/invalid (len=%d)" % len(s)


if __name__ == "__main__":
    # Tiny self-check: python3 wxconfig.py
    print("config:", config_path())
    print("root:", ROOT)
    print("self:", self_wxid(), self_name())
    print(summary_mask_key())
    print("paths:", json.dumps(paths(), ensure_ascii=False, indent=1))
    print("targets:", ", ".join("%s=%s" % (k, v.get("name", "?")) for k, v in targets().items()))
