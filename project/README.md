# project/ —— 工程文件（解密 / 快照 / 增量合并 / 导出 / 报表 / 校验）

本目录是「微信朋友圈导出工具」的工程目录：脚本 + 本地数据 + 配置。
**所有机器相关、个人相关的值都集中在 `config.json`**（数据库主口令、wxid、路径、好友名单、标题与输出文件名）。
本 README 与 [`SETUP.md`](SETUP.md) 不含任何个人数据，可直接分享；开发者本机的 `NOTES.local.md` 仅本地保留，分享包中不含。

> 交付成果在旁边的 `deliverables*/` 目录，由本目录脚本生成；本目录可整体删除后用脚本重建。

## 〇、先跑体检

```bash
cd project/scripts
python3 doctor.py          # 检查配置、依赖、路径、数据库、好友名单，并打印下一步命令
```

`doctor.py` 不会打印主口令；退出码 0=就绪、1=有阻断项、2=配置缺失/无效。

## 一、目录一览

```
project/
├── config.json            本地配置（含主口令；chmod 600；勿分享/提交）
├── config.example.json    配置模板（占位符，可分享）
├── SETUP.md               首次使用/新机器设置步骤（可分享）
├── NOTES.local.md         本机专属信息（账号/路径/哈希/统计；勿分享）
├── requirements.txt       Python 依赖（pycryptodome、openpyxl）
├── data/
│   ├── raw/               微信加密快照（sns.db + -wal + -shm，contact/）
│   ├── decrypted/         解密后的 SQLite（sns_plain.db / contact_plain.db）
│   ├── snapshots/         增量快照历史（append-only，index.json + 每个快照目录）
│   └── state/             每份导出 JSON 更新前的 gzip 备份（保留最近 10 份）
├── scripts/               19 个 Python 脚本（见 §四）
├── logs/                  快照、导出、重建日志
└── README.md              本文件
```

## 二、配置 `config.json`

配置文件查找顺序：

1. 环境变量 `WX_MOMENTS_CONFIG` 指向的文件（可放在任意位置）
2. `project/config.json`

首次使用：`cp project/config.example.json project/config.json`，然后填写；字段说明如下。

| 字段 | 说明 |
|---|---|
| `schema_version` | 固定 `1` |
| `wechat.self_wxid` | 本人微信 wxid（`wxid_` 开头） |
| `wechat.self_name` | 本人显示名（用于报告署名/展示） |
| `wechat.master_key_hex` | 微信 4.x 数据库的 32 字节主口令，**64 位十六进制**（不是登录密码） |
| `wechat.container_dir` | 微信 `xwechat_files` 目录（macOS 默认已给出；Windows/自定义安装按实际填） |
| `wechat.sns_dir` | 可选。直接指定包含 `sns.db` 的目录（自动探测失败时用） |
| `wechat.contact_db_encrypted` | 可选。加密的 contact.db 路径，`decrypt_db.py` 默认读它 |
| `paths.*` | 数据目录（默认 `project/data/...`，一般不用改；可写绝对路径或 `~`） |
| `export.workers` | 媒体并发下载数（默认 8） |
| `export.legacy_snapshot_label` | 旧超集快照在增量合并中记录的来源标签 |
| `self_export.outdir/slug/title/xlsx` | 本人成果的目录、JSON slug、HTML 标题、Excel 文件名 |
| `self_export.expected` | 可选。本人历史统计（帖子/媒体/赞/评论等）用于 `verify_deliverables.py` 严格校验；留 `null` 则只做自洽校验 |
| `targets.<key>` | 好友列表：`name`、`wxid`、`outdir`、`slug`、`title`、`xlsx`，可选 `since`（只导入该日期起的帖子） |

安全约定：

- `config.json` 含主口令，权限应为 `600`（`chmod 600 project/config.json`）；`doctor.py` 会检查并提示。
- 已提供 `.gitignore`，默认忽略 `config.json`、`NOTES.local.md`、`data/`、`deliverables*/`、`*.zip`。
- 脚本不会打印主口令；新快照 `meta.json` 只记录 salt/哈希/行数，不记录派生密钥。

## 三、数据文件

- `data/raw/`：微信原始加密库的快照副本（`sns.db` + WAL/SHM；`contact/` 下为 contact.db）。**不要用当前微信库覆盖它**：微信会持续清理旧帖，旧快照是超集。
- `data/decrypted/`：解密结果。`sns_plain.db` 是增量合并的基础 archive，`contact_plain.db` 提供备注名。
- `data/snapshots/`：每次拍快照生成 `sns_<YYYYMMDD_HHMMSS>/`（raw 副本 + `sns_plain.db` + `meta.json`）并追加到 `index.json`；旧快照永不修改。
- `data/state/`：每次重写导出 JSON 前自动 gzip 备份，保留最近 10 份。
- 全部数据都可用「微信库 + 主口令 + 脚本」重建；`raw/`、`decrypted/`、`snapshots/` 可整体删除后重做（但会丢失“微信已清理帖子”的历史超集价值）。

## 四、脚本与运行顺序

依赖：Python 3.10+（macOS/Python 3.13 已验证）、`pycryptodome`、`openpyxl`：

```bash
python3 -m pip install -r project/requirements.txt
```

### 首次准备（新机器/新账号）

```bash
cd project/scripts
# 方式 A：已有加密库副本
python3 decrypt_sns.py                 # paths.raw_sns_db -> paths.sns_plain
python3 decrypt_db.py                  # wechat.contact_db_encrypted -> paths.contact_plain
# 方式 B：直接从实时微信库拍快照（自动解密 + 建档）
python3 snapshot_db.py --label initial
```

### 日常：增量同步（推荐）

```bash
python3 sync_friends.py                          # 全部 targets：拍快照 → 合并导出 → 重建报告/XLSX → 校验
python3 sync_friends.py --targets lili,caimiaohan
python3 sync_friends.py --no-snapshot --targets lili   # 不拍快照，只按现有快照 + 旧 JSON 合并
python3 sync_friends.py --skip-reports --targets lili  # 只导出，不重建报告
```

输出摘要写入 `logs/sync_summary_*.json`；某个 target 失败不会破坏已有成果。`sync_friends.py` 的校验步骤不检查 zip，打包后建议再手动跑一次 `verify_friend.py --zip`。

### 单步命令

```bash
# 1. 拍快照
python3 snapshot_db.py --label daily
python3 snapshot_db.py --list

# 2. 导出某位好友/某个对象（outdir/title 等一般由 config targets 提供）
python3 export_friend.py --wxid <wxid> --outdir ../deliverables_<name> \
    --title "<name>的朋友圈导出" --slug moments_<name> --xlsx "微信朋友圈_<name>.xlsx" --workers 8

# 3. 重建报表 / Excel（默认读 config；也可用环境变量覆盖）
WX_DELIV=../deliverables_<name> WX_DATA=../deliverables_<name>/moments_<name>.json python3 build_report_all.py
WX_DELIV=../deliverables_<name> WX_DATA=../deliverables_<name>/moments_<name>.json python3 build_xlsx_all.py

# 4. 清理孤儿文件、生成 README、打包、校验
python3 cleanup_orphans.py --dir ../deliverables_<name>
python3 write_deliverable_readme.py --dir ../deliverables_<name> --key <target_key>
python3 make_zip.py --dir ../deliverables_<name> --out ../wechat-moments-<name>-export-YYYY-MM-DD.zip
python3 verify_friend.py --dir ../deliverables_<name> --json moments_<name>.json \
    --zip ../wechat-moments-<name>-export-YYYY-MM-DD.zip
```

### 脚本清单

| 脚本 | 作用 |
|---|---|
| `wxconfig.py` | 统一配置加载（路径/密钥/名单/标题）；其他脚本的唯一配置入口 |
| `doctor.py` | 环境/配置/数据路径预检（只读） |
| `decrypt_sns.py` | SQLCipher4 解密 + 逐页 HMAC 校验 + 合并 WAL → `sns_plain.db` |
| `decrypt_db.py` | 通用单库解密器（如 contact.db） |
| `isaac64.py` | ISAAC-64 CSPRNG（朋友圈 CDN 媒体 XOR keystream） |
| `moments_core.py` | 共享核心：XML 解析、CDN URL、时间格式、路径读取 |
| `snapshot_db.py` | 拍实时微信 sns.db/-wal/-shm → 解密 → `meta.json` + “index.json”（append-only） |
| `archive_core.py` | 增量合并原语（post/like/comment/media 去重键、merge_post、归档保留/复活、JSON 备份） |
| `export_friend.py` | 好友增量导出：并集所有快照 + 旧 JSON，只增不删；媒体复用/重试/外部链接分类；`--since` 设定导入起点 |
| `sync_friends.py` | 一键编排 snapshot → export → report/xlsx → verify |
| `export_all.py` | 本人全量导出（旧超集快照 → 自配目录） |
| `recover_media.py` | 本人幽灵帖跨帖同 md5 媒体恢复 |
| `build_report_all.py` | JSON → HTML（侧栏目录/每行 3 图/点赞评论全展开/归档标记）+ Markdown + CSV |
| `build_xlsx_all.py` | JSON → XLSX（8 个工作表，帖子表带“状态”列） |
| `cleanup_orphans.py` | 删除未被 JSON 引用的 media/ raw_xmls/ 孤儿文件（可 `--dry-run`） |
| `make_zip.py` | UTF-8 文件名打包；jpg/mp4 等媒体 ZIP_STORED，其余 deflate；自动跳过 `.DS_Store` |
| `write_deliverable_readme.py` | 由 JSON 生成交付目录 README.md |
| `verify_deliverables.py` | 本人交付校验（计数、引用、sha256、HTML/CSV/XLSX、zip；预期值来自 config） |
| `verify_friend.py` | 好友交付校验（同上，`--zip` 可选；外部链接媒体不计失败） |

`targets` 配置示例（占位符）：

```json
"targets": {
  "friend1": {
    "name": "好友备注名", "wxid": "wxid_xxx",
    "outdir": "deliverables_friend1", "slug": "moments_friend1",
    "title": "好友1的朋友圈导出", "xlsx": "微信朋友圈_好友1.xlsx",
    "since": ""
  }
}
```

`since` 可给单个好友设置导入起点（如 `2021-09-01`）：更早历史不追溯，已归档内容保持原状。

## 五、增量合并规则（只增不删）

- **帖子键**：`tid` 的无符号 64 位值；同一帖子在所有快照/旧 JSON 中合并为一条。
- **点赞键**：`(username, create_time)`；**评论键**：`comment_id`（缺失时 `(username, create_time, content)`）；**媒体键**：`media_id → md5 → URL` 依次匹配。
- **导入起点**：`targets.<key>.since` 可限定只导入该日期之后的帖子；更早归档内容不删除、不误标 archived。
- 合并时保留旧字段，点赞/评论/媒体取并集；`first_seen_at` / `last_seen_at` / `sources` 记录来源快照。
- 帖子在最新快照中不可见时标 `archived=True`（报告显示「历史归档 · 最新快照未包含」），**不删除**；再次可见会自动复活。
- 媒体复用：旧记录 `status=ok` 且文件存在 → 直接复用；旧失败会重试并记录 `previous_status`。历史归档帖只补“从未尝试”的媒体和网络抖动失败项，已知 4xx 不再反复打 CDN。
- 外部链接媒体（非 `qpic.cn` / `video.qq.com` / `tc.qq.com` 主机，如 QQ 音乐封面）标 `status="external"`，不下载但保留链接。
- 写 JSON 前自动 `backup_json()` 到 `data/state/<slug>/`，保留最近 10 份。

## 六、技术链路（算法参数，与账号无关）

**数据库**：微信 4.x 使用 SQLCipher 4 —— AES-256-CBC + HMAC-SHA512，page_size=4096，reserve=80（IV 16 + HMAC 64）；
KDF = PBKDF2-HMAC-SHA512 256000 轮。主口令（32 字节，来自 `config.json`）配合每个库文件头 16 字节 salt 派生 enc_key；
mac_key = PBKDF2-HMAC-SHA512(enc_key, salt^0x3a, 2, 32)；第 1 页前 16 字节是明文 salt，其余页逐页 HMAC 校验。
不同微信版本/系统可能修改这些参数，`doctor.py` 与 `decrypt_sns.py` 的 `page1_hmac` 校验会第一时间报错。

**朋友圈数据**：`SnsTimeLine(tid INTEGER PRIMARY KEY DESC, user_name TEXT, content TEXT, pack_info_buf TEXT)`；
content 是完整 `SnsDataItem/TimelineObject` XML（contentDesc、createTime、location、ContentObject/mediaList、statisticsData、canvasInfoXml…）；
点赞/评论在 `LocalExtraInfo` 的 like_user_list / comment_user_list。
注意 XML 的 `location` 属性 **latitude/longitude 实际互换**，导出时已纠正。

**媒体 = 腾讯 CDN 加密 + ISAAC-64 流异或**：响应头 `X-Enc: 1`；密钥是 XML 中十进制数字 key（同帖图片共用一个；视频取 `<enc key="...">`）。
用其初始化 ISAAC-64（金比例常量 `0x9e3779b97f4a7c13`），按大端 8 字节取 keystream 异或：图片整文件，视频只前 128 KB。
请求要点：URL `http→https`、`/150→/0`、追加 `?token=<token>&idx=1`、UA 必须是 `MicroMessenger Client`；解密后按最后一个 `FFD9` 截断。
视频可能不带 `X-Enc` 头，先检测原始格式（mp4 直接用），否则异或前 128 KB；评论附图来自评论 XML 的 imagelist。
视频号（finderFeed）媒体在 `wxapp.tc.qq.com`，请求返回 400，无法下载，仅保留元数据与封面。

## 七、校验与复现

```bash
# 本人成果（预期值来自 config.json -> self_export.expected，可留空）
python3 verify_deliverables.py

# 好友成果（可选 --zip）
python3 verify_friend.py --dir ../deliverables_<name> --json moments_<name>.json \
    --zip ../wechat-moments-<name>-export-YYYY-MM-DD.zip

# 全量（本人 + 所有 zip）
python3 verify_deliverables.py
```

两套校验都会检查：计数自洽、每个媒体/XML/缩略图引用可解析、随机 sha256 抽检、HTML 图片与卡片数、CSV 行数、XLSX 工作表行数、zip 完整性。

## 八、隐私与分享

- `project/config.json` 含微信数据库主口令，**绝不要分享或提交**；权限 600。
- `project/data/`、`deliverables*/`、根目录 `*.zip`、`project/NOTES.local.md`、根 `README.md` 都含个人数据，分享工具时删除。
- 可安全分享的文件：本 `README.md`、`SETUP.md`、`config.example.json`、`requirements.txt`、`scripts/*.py`。
- 分享前清单见 `SETUP.md` §7；本机根目录 README 末尾也有一份。一键检查可执行 `grep -rn` 扫描。

## 九、已知限制与排错

| 现象 | 处理 |
|---|---|
| 找不到配置文件 / 字段仍是占位符 | 按 `SETUP.md` 复制模板并填写；`doctor.py` 会指出具体字段 |
| `page1 HMAC mismatch` | 主口令不对，或数据库版本/参数不同；确认 `master_key_hex` 是 64 位十六进制主口令 |
| 找不到微信 `sns.db` | 在 config 里设 `wechat.sns_dir` 或 `wechat.container_dir` |
| 媒体 400/404 | 微信服务器已清理（删帖/重发/过期），无法恢复；报告会红色标注失败媒体 |
| 视频号媒体无法下载 | 微信未开放 `wxapp.tc.qq.com` 媒体，只保留元数据/封面 |
| 大量帖子“历史归档” | 微信清理/可读期限；老快照仍在，属正常“只增不删”行为 |
| 旧内容消失 | 先确认是否在最新微信库中不可见；增量合并不会删除旧帖，只会标 archived |
| 目标目录出现 `.DS_Store` 导致校验失败 | `python3 cleanup_orphans.py --dir <deliv>` 或删除该文件 |
| 磁盘空间不足 | 删 zip（目录完整）；快照是增量合并数据源，不建议删 |
