# 微信朋友圈导出工具 · 分享包（2026-10-25）

这是**脱敏后的完整工具包**：不含任何账号、密钥、本机路径或朋友圈数据，可直接拷贝给别人。

```
wechat-moments-tool/
├── project/
│   ├── scripts/               19 个 Python 脚本（解密 / 快照 / 导出 / 报表 / 校验）
│   ├── config.example.json    配置模板（需要对方填自己的信息）
│   ├── README.md              通用技术说明与排错
│   ├── SETUP.md               首次使用 / 新机器设置步骤（先读这个）
│   └── requirements.txt       Python 依赖
└── .gitignore                 防止误提交配置与数据
```

## 三步开始

```bash
# 1. 安装依赖
python3 -m pip install -r project/requirements.txt

# 2. 创建自己的配置（含微信主口令，权限设为 600）
cp project/config.example.json project/config.json
chmod 600 project/config.json

# 3. 按 SETUP.md 填写 config.json，然后体检
cd project/scripts
python3 doctor.py
```

详细步骤（获取/解密数据库、导出本人、增量导出好友、打包、校验、排错）见 **`project/SETUP.md`**；
算法与脚本说明见 **`project/README.md`**。

## 说明

- 本包只包含工具代码与占位符模板，**没有** `config.json`、`data/`、`deliverables*/`、`*.zip`。
- 所有路径以本文件夹为根目录解析；把这个文件夹整体拷到任意位置都能用。
- 使用时请只处理**自己的**微信数据，并遵守相关隐私约定。
