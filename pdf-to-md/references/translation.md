# 翻译参数与行为

翻译是**可选**步骤。模型、端点、Key 全部是运行时参数，换模型不需要改代码。

## 1. 三个参数

| 参数 | 环境变量 | 说明 |
|---|---|---|
| `--base-url` | `PDF2MD_BASE_URL`（兼容 `OPENAI_BASE_URL`） | OpenAI 兼容端点，**要带 `/v1`** |
| `--model` | `PDF2MD_MODEL` | 模型名，原样透传 |
| `--api-key` | `PDF2MD_API_KEY`（兼容 `OPENAI_API_KEY`） | 密钥，只以 Bearer 头发送 |

解析顺序（高到低）：命令行参数 → 环境变量 → `PDF2MD_SECRETS` 指定的 JSON → `./.secrets/pdf2md.json` → `~/.config/pdf2md/secrets.json` → `./.env`。

`./.env` 只解析、不写入 `os.environ`，避免污染同进程其它工具。

### 常用切换写法

```bash
# 命令行（临时换模型）
python3 scripts/pdf2md.py paper.pdf --translate \
  --base-url https://api.deepseek.com/v1 --model deepseek-chat --api-key sk-…

# 环境变量（一次会话内生效）
export PDF2MD_BASE_URL=https://api.deepseek.com/v1
export PDF2MD_MODEL=deepseek-chat
export PDF2MD_API_KEY=sk-…

# 写入凭据文件（长期，且不进 git）
cat > .secrets/pdf2md.json <<'JSON'
{"mineru_token":"…","base_url":"…","model":"…","api_key":"…"}
JSON
```

`--doctor` 会打印每个值**来自哪里**（命令行 / 环境变量 / 具体文件），密钥只显示 `前6位…后4位`。

## 2. 端点拼接

`base_url` 末尾的 `/` 会被去掉：

- 已经以 `/chat/completions` 结尾 → 原样使用；
- 其它情况 → 追加 `/chat/completions`。

所以 `https://api.example.com/v1` → `https://api.example.com/v1/chat/completions`。若服务商要求别的路径，直接把完整 URL 传给 `--base-url`。

## 3. 网关兼容性（实测踩过的坑）

| 现象 | 原因 | 代码里的处理 |
|---|---|---|
| HTTP 403 + `error code: 1010`（Cloudflare 页面，不是 JSON） | 默认 `Python-urllib/x.y` UA 被拦 | 所有请求固定发送 `User-Agent: pdf2md/0.2 (+https://mineru.net)` |
| HTTP 400 `MissingSessionID` | opencode.ai 需要 `x-opencode-session` | 仅当 host 含 `opencode.ai` 时自动附加每次运行一个的 session id |
| 其它私有网关需要自定义头 | — | `--api-header "Name: value"`（可重复） |

这些是**传输层事实**，不是配置偏好；改动前先看 `references/mineru-api.md` 与本文件。

## 4. 分块

- 先按空行切成段落，再按 `--chunk-size`（默认 3000 字符）装箱。
- **绝不在**代码围栏内部、`$$` 块级公式内部切开；闭合的围栏/公式不会导致后续不再切分（有回归测试）。
- 单个超长段落不强行切，整段作为一个块。
- 块按原文顺序拼接回 `_zh.md`（`\n\n` 连接），并发完成顺序不影响输出顺序。

## 5. 检查点

- 路径：`<out_root>/.checkpoints/<stem>-<context前20位>.json`。
- `context = sha256(base_url|model|chunk_size|源PDF sha256)`。
- 只有 `model`、`base_url`、`context`、`chunks` 四项完全一致才恢复；任一项变化视为新任务。
- 每完成一块就原子写盘，中断后重跑只补缺失块（日志会打印「续跑：已恢复 n/m」）。
- 全部完成且校验通过后删除检查点。

## 6. 失败语义

| 情况 | 结果 |
|---|---|
| 端点返回非 200 | `TranslationError`，报 HTTP 状态与响应片段 |
| 返回空 `content`（例如只有 `reasoning_content`） | `TranslationError`，不写中文文件 |
| 某一块抛异常 | 已完成的块留在检查点，英文与检查点保留，`_zh.md` 不写 |
| 中文文件已生成但校验失败（例如图片序列被改动） | 丢弃临时中文文件，报 `image_reference_parity_mismatch` 等 code |

**不会**出现「翻译了一半的 `_zh.md`」：中文文件先写成临时文件，校验通过后才原子替换。

## 7. 提示词要点

模型被要求：保留 Markdown 语法与标题层级、公式与代码原样、图片引用不增不减不改序、表格分隔行与原列数不变、专有名词保留英文、不增删段落、只输出译文。推理模型常见的 `<think>…` 块会被剥离（含被截断的未闭合块）。

## 8. 成本与并发

- 默认 `--workers 8`；远端限流时可降到 2–4。
- 并发只影响请求数，不改变输出顺序。
- 一份 70 KB 的英文 Markdown 约 26 块，实测 `deepseek-v4.1-flash` 在 6 并发下约 2 分钟完成。
