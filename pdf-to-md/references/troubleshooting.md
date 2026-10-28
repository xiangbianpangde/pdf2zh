# 故障处置

按「现象 → 原因 → 处置」查。所有命令都在 skill 根目录下执行（`scripts/pdf2md.py`）。

## 1. 凭据与鉴权

| 现象 | 原因 | 处置 |
|---|---|---|
| `HTTP 401「user token expired」` | MinerU Token 过期或其前缀不对 | 重新申请：<https://mineru.net/apiManage/token>；写入 `.secrets/pdf2md.json` 的 `mineru_token` |
| `code=A0202` / `A0211` | Token 错误 / 过期 | 同上；注意 header 是 `Bearer <token>` |
| `400「MissingSessionID」` | 翻译端点（opencode.ai）要求 `x-opencode-session` | 代码已自动附加；自建网关时用 `--api-header "x-opencode-session: <id>"` |
| `403` + `error code: 1010`（HTML 不是 JSON） | 网关按 UA 拦截，默认 `Python-urllib` 被拒 | 代码已固定 `User-Agent`；若仍被拦，用 `--api-header "User-Agent: ..."` 覆盖 |
| `缺少 MinerU Token` | 没配置，或只配了翻译凭据 | `--mineru-token` / `MINERU_TOKEN` / `.secrets` 三选一；或改用 `--ocr flash`（无图片） |
| `翻译需要 Base URL、模型名、API Key` | 三者缺一 | `--base-url/--model/--api-key`，或写 `.secrets`；只要 OCR 就去掉 `--translate` |

`python3 scripts/pdf2md.py --doctor` 会打印每个值来自哪个来源（命令行 / 环境变量 / 具体文件），并做一次真实连通性探测。

## 2. MinerU 解析

| 现象 | 原因 | 处置 |
|---|---|---|
| `file page count exceeds API limit (20 pages)` | flash 通道上限 20 页 | `--pages 1-20` 只解析前 20 页，或 `--ocr precision`（上限 200 页） |
| `-60005` / `文件超过 200 MB` | 超过精准解析上限 | 拆分 PDF，或先压缩 |
| `-60006` / `页数超过 200 页` | 超过精准解析上限 | `--pages` 指定范围 |
| `-60004` / `空文件` | 0 字节或损坏 | 检查源文件 |
| `-60002` | 后缀识别失败 | 文件名要带 `.pdf` |
| `-60009` / `-10001` | 队列满 / 服务异常 | 稍后重试 |
| `-60018` / `-60019` | 当日额度用尽 | 次日再试；flash 额度与精准解析分开计 |
| `-60012` | 查询了不存在的任务 | 只需重跑；缓存/检查点不受影响 |
| `等待 MinerU 结果超时` | 队列慢，不是解析失败 | 加大 `--timeout`（默认 1800 s）后重试；OCR 缓存不受影响 |

解析失败不会留下半成品：空运行目录会被清理，交付目录不会出现。

## 3. 交付校验

| code | 原因 | 处置 |
|---|---|---|
| `missing_markdown` | 英文 md 缺失或为空 | 重跑；检查 `_meta/manifest.json` 是否显示解析成功 |
| `unreadable_markdown` | 不是合法 UTF-8 | 多半是下载截断，删掉 `.cache` 重跑 |
| `missing_image` | md 引用了不存在的图片 | 重跑；若持续出现，检查图片是否在 `images/` 下被外部删除 |
| `external_image` | md 引用了 `http(s)://` 或 `data:` 图片 | 用 precision 通道重跑；flash 结果不应出现本地引用 |
| `non_portable_image_path` | 绝对路径、`..`、`.cache` | 同上，重跑归位步骤 |
| `image_outside_assets` | 图片经软链接指向 `images/` 外 | 删除软链接后重跑 |
| `extension_mismatch` | 后缀与真实编码不符 | 重跑归位步骤（会自动改正后缀） |
| `undecodable_image` | 图片截断或编码不支持 | 重新解析（删 `.cache` 后重跑） |
| `image_reference_parity_mismatch` | 中英两版图片数量或顺序不一致 | 删掉 `_zh.md` 后重译；仍失败说明译文改动了结构，降低 `--chunk-size` |

校验结果在 `_meta/validation.json`。`ok: false` 时**不要**把目录当交付物。

## 4. 翻译

| 现象 | 原因 | 处置 |
|---|---|---|
| `翻译返回空内容` | 模型只回了 `reasoning_content`，或触发内容过滤 | 换模型或降低 `--chunk-size` 重试 |
| `连接翻译端点失败` | 网络/DNS/TLS | 检查 `--base-url`；`--doctor` 会做一次真实探测 |
| `HTTP 404` | base_url 少写 `/v1` | 补全；或直接给完整的 `.../chat/completions` |
| `HTTP 401/403` | key 错误或来源限制 | 换 key；网关限制来源时用 `--api-header` |
| `HTTP 429` | 限流 | 已自动重试 3 次（指数退避）；仍失败就降低 `--workers` |
| 译文里出现 `以下是翻译` | 模型没遵守约束 | 换模型；提示词已明确禁止 |
| 译文把公式/图片改了 | 模型越界 | 校验会拦下（parity mismatch）；重译或换模型 |

翻译失败时英文与检查点保留，`_zh.md` 不会写；重跑同一命令即续跑。

## 5. 输出目录与重跑

| 现象 | 原因 | 处置 |
|---|---|---|
| `输出目录已属于另一份 PDF` | 同名但内容不同（sha256 不一致） | 换 `-o`，或确认后加 `--force` |
| `输入文件映射到同一输出目录` | `a.pdf` 与 `a.PDF` 映射到同一 stem | 重命名其中一个 |
| `路径越界` / `拒绝通过软链接写入` | 输出路径逃出根目录或经过软链接 | 换一个真实目录 |
| 想彻底重做 | 完成标记会跳过 | `--force` |
| 想只重做翻译 | 改翻译参数即可让指纹失效 | 直接重跑（OCR 缓存仍复用） |
| 磁盘空间 | 缓存会保留解包结果 | 删 `<out>/.cache` 安全，只影响下次是否重新调 MinerU |

## 6. 快速自查顺序

```bash
python3 scripts/pdf2md.py --doctor           # 凭据 + 连通性
python3 -m unittest discover -s tests -t tests   # 离线回归是否仍然健康
python3 scripts/pdf2md.py x.pdf -o /tmp/try --json   # 单文件最小复现
```
