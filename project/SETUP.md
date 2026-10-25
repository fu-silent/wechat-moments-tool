# SETUP.md —— 首次使用 / 新机器设置（可分享）

本文件帮助你在自己的电脑上运行「微信朋友圈导出工具」。
所有个人/机器相关的值都在 `project/config.json` 里；**本文件、`README.md`、`config.example.json` 和脚本本身都不含任何人的私密数据。**

> 使用前请确认这是**你自己的微信数据**，仅作个人归档；请勿导出、传播他人隐私。

---

## 1. 你需要准备

| 要求 | 说明 |
|---|---|
| 操作系统 | 目前仅在 **macOS + 微信 4.1.15** 上实测过；其他版本/系统的数据库参数可能不同（先跑 `doctor.py` 看 `page1_hmac` 是否通过） |
| Python | 3.10+（3.13 已验证） |
| Python 依赖 | `pycryptodome`、`openpyxl`（见 `requirements.txt`） |
| 微信主口令 | 32 字节数据库主口令的 64 位十六进制形式（不是微信登录密码）。用你原来的解密工具/脚本获得 |
| 微信数据目录 | 默认 `~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files`（macOS） |

安装依赖：

```bash
python3 -m pip install -r project/requirements.txt
```

---

## 2. 创建并填写配置

```bash
cd project
cp config.example.json config.json
chmod 600 config.json          # 含主口令，必须限制权限
```

然后编辑 `config.json`，至少填写以下字段（其余保持默认即可）：

| 字段 | 填什么 | 示例 |
|---|---|---|
| `wechat.self_wxid` | 你的微信 wxid | `wxid_abcdefg12345678` |
| `wechat.self_name` | 你的显示名 | `张三` |
| `wechat.master_key_hex` | 64 位十六进制主口令 | `0123…cdef`（共 64 个字符） |
| `wechat.container_dir` | 微信 `xwechat_files` 目录 | 默认值通常可用；找不到就在下一步用 `sns_dir` 指定 |
| `wechat.sns_dir` | 可选；直接指定包含 `sns.db` 的目录 | 自动探测失败时必填 |
| `wechat.contact_db_encrypted` | 可选；加密的 `contact.db` 路径 | 用于显示备注名 |
| `self_export.title` | 你的报告标题 | `我的朋友圈全量导出` |
| `targets` | 想导出朋友圈的好友 | 见 §5；不导出好友可先留空对象 `{}` |
| `self_export.expected` | 可选；你自己的历史统计 | 第一次建议保持 `null` |

`config.json` 是唯一含密钥的文件：**不要发人、不要提交到 git、不要放公开网盘**。

---

## 3. 准备本地数据库（三选一）

### 方式 A：拍实时快照（推荐，能自动解密 + 建立增量快照）

```bash
cd project/scripts
python3 snapshot_db.py --label initial
```

脚本会从 `wechat.sns_dir`（或 `wechat.container_dir` 自动探测）复制 `sns.db/-wal/-shm`，
解密到 `project/data/snapshots/sns_<时间>/sns_plain.db`，并写快照索引。

### 方式 B：你有已复制出来的加密 `sns.db`

把 `sns.db` 和 `sns.db-wal` 放进 `project/data/raw/`，然后：

```bash
cd project/scripts
python3 decrypt_sns.py            # raw_sns_db -> paths.sns_plain
```

### 方式 C：自定义路径

```bash
python3 decrypt_sns.py <加密sns.db> <sns.db-wal> <输出sns_plain.db>
```

### 解密 contact.db（备注名/昵称显示需要）

```bash
cd project/scripts
python3 decrypt_db.py --db <你的contact.db> --out ../data/decrypted/contact_plain.db
# 或在 config 里填好 wechat.contact_db_encrypted 后直接：
python3 decrypt_db.py
```

没有 contact.db 也能导出，只是报告里显示昵称而不是“备注（昵称）”。

---

## 4. 体检

```bash
cd project/scripts
python3 doctor.py
```

它只读检查：Python/依赖、配置与占位符、主口令格式、数据路径与数据库完整性、微信目录、好友名单。
全部 `[OK]` 后即可导出；`[BLOCK]` 按提示修复。

---

## 5. 导出本人朋友圈

```bash
cd project/scripts
python3 export_all.py             # 下载并解密全部媒体（可能较久，取决于数量和图床速度）
python3 build_report_all.py       # HTML / Markdown / CSV
python3 build_xlsx_all.py         # Excel
python3 verify_deliverables.py    # 校验（expected 留空时只做自洽校验）
```

产物在 `config.self_export.outdir`（默认 `deliverables/`）：
`*.html`（首选可视化报告，需与目录一起移动）、`*.md`、`*.json`、`*.xlsx`、`csv/`、`media/`、`raw_xmls/`。

> 第一次跑建议先在 `config.json` 里把 `export.workers` 调小（如 3），确认网络/失败情况后再调大。

---

## 6. 导出好友朋友圈（增量、只增不删）

先在 `config.json` 的 `targets` 中加入好友：

```json
"targets": {
  "friend1": {
    "name": "好友备注名",
    "wxid": "wxid_xxxxxxxx",
    "outdir": "deliverables_friend1",
    "slug": "moments_friend1",
    "title": "好友1的朋友圈导出",
    "xlsx": "微信朋友圈_好友1.xlsx",
    "since": ""
  }
}
```

然后：

```bash
cd project/scripts
python3 sync_friends.py                    # 拍新快照 → 增量导出 → 重建报告/XLSX → 校验
python3 sync_friends.py --no-snapshot      # 用现有快照，不拍新快照
python3 sync_friends.py --targets friend1  # 只跑一个 target
```

完成后可在目标目录里生成 README 并打包：

```bash
python3 write_deliverable_readme.py --dir ../deliverables_friend1 --key friend1
python3 make_zip.py --dir ../deliverables_friend1 --out ../wechat-moments-friend1-export-YYYY-MM-DD.zip
python3 verify_friend.py --dir ../deliverables_friend1 --json moments_friend1.json \
    --zip ../wechat-moments-friend1-export-YYYY-MM-DD.zip
```

---

## 7. 分享前自检（重要）

工具本身可以分享；**数据、配置、结果不能**。分享前建议：

1. 删除/清空 `project/config.json`（或直接不发它，让对方用 `config.example.json` 复制一份）。
2. 删除 `project/NOTES.local.md`（如果对方拿到了）、`project/data/`、所有 `deliverables*/`、根目录 `*.zip`、根 `README.md`。
3. 只保留：`project/scripts/`、`project/config.example.json`、`project/requirements.txt`、`project/README.md`、`project/SETUP.md`、`.gitignore`（可选）。
4. 跑一次泄漏扫描（应只看到 `config.example.json` 里的占位符和代码里的字段名）：

```bash
cd /path/to/tool
grep -rn -E 'master_key_hex[^"]*"[0-9a-f]{64}"|wxid_[a-z0-9]{8,}|/Users/[^/ ]+' \
     project/scripts project/README.md project/SETUP.md project/config.example.json || echo "CLEAN"
python3 project/scripts/doctor.py 2>/dev/null | grep -i 'master key' || true
```

5. 确认 `config.json` 权限为 `600`（仅自己可读）。

---

## 8. 常见问题

见 `project/README.md` §九「已知限制与排错」，常见如：主口令不对（`page1 HMAC mismatch`）、
找不到微信目录（填 `wechat.sns_dir`）、媒体 404（微信服务器已清理）、视频号无法下载（微信未开放）等。
