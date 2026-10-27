# pdf2zh → PDF → Markdown Skill

把 PDF 转成可移植 Markdown 的 **Pi Skill**：MinerU 云 API 解析（保留标题结构、表格、公式、图片）+ OpenAI 兼容模型翻译（可选）。

> **状态：M0 立项**。目前只有契约、资料整理与骨架，`scripts/` 未实现，**还不能安装使用**。
> 计划见 [docs/PLAN.md](docs/PLAN.md)，阶段与门禁见 [docs/ROADMAP.md](docs/ROADMAP.md)。

## 目录

```
.
├── README.md                 # 本文件：项目说明
├── docs/
│   ├── PLAN.md               # 计划文档：目标、范围、契约、决策、验收
│   └── ROADMAP.md            # 路线图：M0–M5 与门禁
└── pdf-to-md/                # 唯一的 Pi Skill 包（安装单元）
    ├── SKILL.md              # 入口：触发条件、通道判断、失败报告
    ├── CHANGELOG.md
    └── references/
        └── mineru-api.md     # MinerU 官方文档整理稿（含来源与抓取时间）
```

## 为什么是 Skill 而不是命令行工具

消费方是 agent，不是人肉敲命令。Skill 的 `description` 就是触发面：用户说「把这篇论文转成 md，图片留着」，agent 自己判断该用、该走哪条通道、该报什么失败。API 细节放 `references/` 渐进加载，确定性动作落 `scripts/`，避免每轮让模型手写 HTTP 调用。

## 两条硬约束（先看这个）

1. **图片只有「精准解析 API」能给**。免 Token 的 Agent 轻量接口只返回 Markdown 链接，没有 zip、没有 `images/`、没有结构 JSON。所以「保留图片」= 必须有 MinerU Token。
2. **Agent 轻量接口的调用契约已变**：现行是 `POST /api/v1/agent/parse/file`（JSON 取签名 URL）+ `PUT` 上传 + 轮询，不是早前的 multipart 直传。

细节与错误码见 [pdf-to-md/references/mineru-api.md](pdf-to-md/references/mineru-api.md)。

## 现在可以做的检查

```bash
# skill 包结构校验（0 error / 0 warning 才算过）
python3 ~/.pi/agent/skills/task/skill-authoring/scripts/validate.py --strict pdf-to-md
```

M1 起才会有的检查（`pytest`、真实 PDF 端到端）见路线图门禁。

## 与旧实现的关系

`main@bd6d939` 是一套 2964 行的 Python 包（CLI + Gradio UI + 契约测试）。本项目**只继承它被真实验证过的行为**——图片归一化与引用重写、六类交付硬校验、「不在代码围栏/块级公式里切块」的分块规则、翻译检查点语义、凭据解析顺序——不继承 argparse 外壳与 WebUI。

旧代码完整保留在 `main` 分支与 `bd6d939`；本分支 `rewrite/v2` 从空树重建。
