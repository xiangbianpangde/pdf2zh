# pdf2zh → PDF → Markdown Skill

把 PDF 转成可移植 Markdown 的 **Pi Skill**：MinerU 云 API 解析（保留标题结构、表格、公式、图片）+ 任意 OpenAI 兼容模型翻译（可选，参数随时可换）。

> 状态：**M1–M4 完成**：已部署为 Pi skill，并在真实会话中用 `/skill:pdf-to-md` 跑通。压测见 [docs/M3-STRESS.md](docs/M3-STRESS.md)，阶段记录见 [docs/ROADMAP.md](docs/ROADMAP.md)。

## 两条硬约束（先看这个）

1. **图片只有「精准解析 API」能给。** 免 Token 的 Agent 轻量接口只返回 Markdown，实测输出结构完整但**零图片引用**、没有结构 JSON。所以「保留图片」= 必须有 MinerU Token。
2. **Agent 轻量接口的调用契约已变**：现行是 `POST /api/v1/agent/parse/file`（JSON 取签名 URL）+ 裸 `PUT` 上传 + 轮询，不是早前的 multipart 直传。

细节与错误码见 [pdf-to-md/references/mineru-api.md](pdf-to-md/references/mineru-api.md)。

## 快速开始

```bash
# 凭据：三选一（也可用环境变量或命令行）
cat > .secrets/pdf2md.json <<'JSON'
{"mineru_token": "...", "base_url": "https://api.example.com/v1", "model": "some-model", "api_key": "..."}
JSON

python3 pdf-to-md/scripts/pdf2md.py --doctor          # 真实连通性自检
python3 pdf-to-md/scripts/pdf2md.py paper.pdf -o out  # 只转英文
python3 pdf-to-md/scripts/pdf2md.py paper.pdf -o out --translate \
  --base-url https://api.example.com/v1 --model some-model --api-key sk-...
```

产出：

```
out/paper/
├── paper.md        英文 Markdown（图片链接已改写）
├── paper_zh.md     中文（--translate 时才有）
├── paper.pdf       源 PDF 副本
├── images/         归一化图片
└── _meta/          manifest / validation / completion / content_list
```

## 安装（已装好，这里是复现方式）

```bash
ln -sfn /Users/xbpd/Projects/pdf2zh/pdf-to-md ~/.pi/agent/skills/pdf-to-md
# 重新加载 Pi 后，可用 /skill:pdf-to-md 显式调用，或直接描述任务让它自己路由
```

软链指向 **skills 根目录**（`~/.pi/agent/skills/pdf-to-md`），不是 `task/` 之类子目录；仓库 `main` 是唯一内容来源，改完代码无需重装。

验证是否被 Pi 看见（用 Pi 自己的 loader，不是本地解析器）：

```bash
node ~/.pi/agent/skills/task/skill-authoring/scripts/skill_validator/pi_discover.mjs \
  /opt/homebrew/lib/node_modules/@earendil-works/pi-coding-agent/dist/index.js \
  ~/.pi/agent/skills pi | python3 -c "import json,sys; d=json.load(sys.stdin); print([s['name'] for s in d['skills']], len(d['diagnostics']))"
```

## 实测结果（真实 PDF，16 页 / 5.2 MB）

| 步骤 | 结果 |
|---|---|
| precision OCR | 21 s，67 张图，校验 0 issue |
| flash OCR | 22 s，结构完整（31 标题 / 7 表格 / 6 公式），**0 图** |
| 翻译（`deepseek-v4.1-flash`，26 块 / 6 并发） | 122 s，标题 31/31、HTML 表格 7/7、块级公式 6/6、图片序列 57/57 一致 |
| 错误 Token | 退出码 1，`HTTP 401「user token expired」`，不留交付目录 |
| 批量 3 份并发（flash） | 47.3 s，3/3 成功 |
| 复用（完成标记 / OCR 缓存） | 均 0.13 s、**0 次外部调用** |
| 失败隔离（4 份混入超页文件） | 3 成功 1 失败，退出码 1，失败者无交付目录 |
| 翻译并发 1 → 8 | 102.2 s → 38.8 s（**2.6×**），端点 16 并发无 429 |
| 8 份并发（flash） | 44.1 s vs 串行 269.5 s（**6.1×**），8/8 成功，峰值 RSS 96.9 MB |
| 翻译中途 SIGKILL 后重跑 | 只补缺失块，已完成块 **0 次**重发，最终交付有效 |
| Pi 内真实调用（`/skill:pdf-to-md`） | 直接产出交付目录，模型报告与磁盘产物一致 |

## 目录

```
.
├── README.md
├── docs/
│   ├── PLAN.md               # 目标、范围、契约、决策、验收
│   ├── ROADMAP.md            # M0–M5 与门禁
│   └── M3-STRESS.md          # 批量/并发/缓存/限流压测报告
└── pdf-to-md/                # Pi Skill 包（安装单元）
    ├── SKILL.md              # 触发条件、判断流程、失败报告
    ├── CHANGELOG.md
    ├── references/
    │   ├── mineru-api.md     # 官方文档整理稿（含来源与抓取时间）
    │   ├── output-contract.md# 九类校验码、命名、缓存与重跑
    │   ├── translation.md    # 模型参数、端点拼接、网关兼容、检查点
    │   └── troubleshooting.md # 现象 → 原因 → 处置
    ├── scripts/              # 零第三方依赖，Python 3.10+ 标准库
    └── tests/                # 57 项离线用例 + resume_smoke.py / trigger_check.py（opt-in）
```

## 验证

```bash
# 离线测试（不需要网络与凭据）
cd pdf-to-md && python3 -m unittest discover -s tests -t tests

# 真实中断续跑（离线，几秒，会真的 SIGKILL 一个子进程）
cd pdf-to-md && python3 tests/resume_smoke.py

# 触发检查：静态描述检查 + 路由模拟（--confirm 才会发起 6 个短请求）
python3 pdf-to-md/tests/trigger_check.py --dry-run
python3 pdf-to-md/tests/trigger_check.py --confirm

# skill 包结构校验（需要指向本机 Pi 安装位置）
PI_CODING_AGENT_PACKAGE=/opt/homebrew/lib/node_modules/@earendil-works/pi-coding-agent \
  python3 ~/.pi/agent/skills/task/skill-authoring/scripts/validate.py --strict pdf-to-md
```

## 与旧实现的关系

`main@bd6d939` 是一套 2964 行的 Python 包（CLI + Gradio UI + 契约测试）。本项目只继承它**被真实验证过的行为**——图片归一化与引用重写、交付硬校验、不在代码围栏/块级公式里切块、翻译检查点语义、凭据解析顺序——不继承 argparse 外壳与 WebUI，并改为零第三方依赖。

旧代码完整保留在 `main` 与 `bd6d939`。
