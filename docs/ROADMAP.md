# ROADMAP — PDF → Markdown Skill

> 阶段门驱动，不写虚构日期。**进入下一阶段的唯一条件是本阶段门禁全部通过。**

## 进度

| 阶段 | 状态 |
|---|---|
| M0 立项与底线 | ✅ 完成（2026-10-27） |
| M1 单通道跑通 | ⬜ 未开始 |
| M2 可选翻译 | ⬜ 未开始 |
| M3 批量与缓存 | ⬜ 未开始 |
| M4 Skill 收口 | ⬜ 未开始 |
| M5 扩展 | ⬜ 未开始 |

## 里程碑总览

| 阶段 | 目标 | 主要交付物 | 门禁 |
|---|---|---|---|
| M0 | 立项：让后面的工作有依据 | 目录、MinerU 资料整理稿、PLAN、ROADMAP、SKILL 草案 | 资料可追溯（来源 URL + 抓取时间）；计划含可验收条目；目录无空壳 |
| M1 | 一份真实 PDF 端到端跑通（不翻译） | `mineru_client.py`、`organize.py`、`validate.py`、`pdf2md.py`、`doctor.py`、`references/output-contract.md`、`tests/` | 真实 precision 解析出交付目录；`validation.json.ok == true`；无 Token 时报错不产半成品 |
| M2 | 可选翻译 | `translate.py`、`references/translation.md`、分块/检查点测试 | 中英图片序列一致；中断续跑不重复消耗；失败不留伪完成文件 |
| M3 | 批量与缓存 | 多文件编排、内容指纹缓存、并发与限流、`--refresh` | 3 份 PDF 并发跑通；缓存命中不重复调 API；配额内不触发限流 |
| M4 | Skill 收口 | SKILL.md 定稿、`troubleshooting.md`、触发正负测试、validator | `validate.py --strict` 0 error 0 warning；正例命中/负例不命中 |
| M5 | 扩展（可选） | HTML 预览、`extra_formats`、flash 策略成熟化、benchmark、兼容层 | 按需立项，不阻塞主线 |

依赖：M1 → M2、M1 → M3、M2+M3 → M4。

---

## M0 立项与底线 ✅

交付物：

- [x] `pdf-to-md/references/mineru-api.md` — 官方文档整理稿（两套 API、参数、状态机、错误码、语言表、待实测项）
- [x] `docs/PLAN.md` — 核心目标、范围、契约、决策与验收
- [x] `docs/ROADMAP.md` — 本文件
- [x] `pdf-to-md/SKILL.md` — 草案（明确标注未实现）
- [x] `README.md`、`pdf-to-md/CHANGELOG.md`

门禁核对：资料有来源与抓取时间 ✅；计划有可验收项 ✅；无空目录空文件 ✅。

## M1 单通道跑通（下一步）

范围：**只做 precision + 不翻译**。把交付契约钉死。

任务：

1. `references/output-contract.md`：目录、命名、`images/` 引用规则、六类硬校验、`_meta/` 内容
2. `scripts/mineru_client.py`：`POST /api/v4/file-urls/batch` → PUT（不带 `Content-Type`）→ 轮询 `GET /api/v4/extract-results/batch/{id}` → 下载 zip → 解包（防 zip-slip）
3. `scripts/organize.py`：解析 md 图片引用 → 真实编码嗅探 → 复制进 `images/` → 重写链接 → 写 `manifest.json`
4. `scripts/validate.py`：缺图 / 外链 / 绝对路径与 `..` / 扩展名与 MIME 不符 / 不可解码 / 中英序列不一致
5. `scripts/pdf2md.py` + `scripts/doctor.py`：参数、退出码（0/1/2）、凭据自检
6. `tests/`：图片归一化、链接重写、校验六类、zip-slip、参数校验
7. `pdf-to-md/SKILL.md`：加执行段（脚本调用与输出判读），仍不安装

门禁：

- 真实 PDF（样例：`~/Downloads/1-s2.0-S0141938225001878-main.pdf`，5.2 MB / 16 页）跑通，`validation.json.ok == true`
- 图片数与 md 引用数一致，抽看 3 张能打开
- 删掉 `MINERU_TOKEN` 与 `.secrets` 后重跑：明确报错、不留半成品目录
- `pytest` 全绿

风险：MinerU 排队时间不可控 → 轮询超时与解析失败分开报。

## M2 可选翻译

任务：

1. `references/translation.md`：模型选择、分块规则、保护规则、检查点语义、失败语义
2. `scripts/translate.py`：按空行分块（不在代码围栏/块级公式/表格内切）、并发、检查点、指纹失效
3. 结构保护：公式、代码块、图片引用、表格分隔符、URL 不译
4. 测试：闭合定界符不关闭分块、跨行围栏不切开、检查点只恢复缺失块、上下文变化则失效
5. `pdf2md.py --translate [--target zh]`

门禁：

- 同一份 PDF：不翻译产出 A；翻译产出 B；B 的图片引用数量与顺序等于 A
- 翻译中途 kill，重跑只补缺失块（日志可见「已恢复 n/m」）
- 翻译失败时 `_zh.md` 不存在，英文与检查点保留

## M3 批量与缓存

任务：

1. 多 PDF 输入、`--parallelism`、每文件独立目录
2. 缓存：`<out>/.cache/<源 sha256 + 通道参数>/`，含 receipt + READY 标记；`--refresh` 走新 run 目录，不销毁旧缓存
3. 限流：MinerU 单批 ≤50、账号 1000 页/天；翻译并发上限
4. 可选：批量时镜像输入目录树（需用户确认后再做）
5. 测试：缓存命中不调 API、`--refresh` 不复用、同名不同内容不误命中

门禁：3 份 PDF 并发成功；第二次运行全命中缓存且输出一致；配额内无 429/限流错误。

## M4 Skill 收口

任务：

1. `SKILL.md` 定稿：触发边界（含 3 条反例）、决策流程、失败报告格式、证据边界
2. `references/troubleshooting.md`：错误码 → 处置（对齐 `mineru-api.md` §2.9/§3.4）
3. 触发正负测试：正例「把这篇论文转成 md 保留图片」「PDF 转 Markdown 并翻译成中文」；负例「docx 转 md」「解释这篇论文」「PDF 页面排版复刻」
4. 跑 `python3 ~/.pi/agent/skills/task/skill-authoring/scripts/validate.py --strict pdf-to-md`，0 error 0 warning
5. README 安装说明（复制/软链到 `~/.pi/agent/skills/task/pdf-to-md`）

门禁：validator 0/0；正负触发测试通过；`CHANGELOG.md` 有对应版本段。

## M5 扩展（不阻塞主线）

| 项 | 价值 | 前置 |
|---|---|---|
| `--html` 阅读预览 | 便于审阅 | M2 |
| `extra_formats`（docx/html/latex） | 直接要 Word 的用户 | M1 |
| flash 通道成熟化 | 无 Token 也能用 | M1 实测页数上限 |
| OCR/翻译 benchmark | 选型有据 | M3 |
| `pi-paper-translator` 兼容层 | 修好坏掉的 `pdf_translate` | 用户决定是否要 |

## 首次实跑的准备清单

1. 本 worktree 的 `.env` / `.secrets` 已按指令删除 → 从 `main` 复制，或直接导出 `MINERU_TOKEN` / `MINIMAX_API_KEY`
2. 确认样例 PDF 存在：`~/Downloads/1-s2.0-S0141938225001878-main.pdf`
3. 输出根目录用临时目录，避免污染 home
4. 跑完核对：`_meta/validation.json`、`ls images | wc -l`、md 内 `![](images/` 计数

## 变更管理

- 契约（§5.2 目录结构、校验规则）变更必须先改 `docs/PLAN.md`，再改代码与测试
- 阶段门禁未过时不开新阶段；发现门禁不成立时，先记录到 PLAN §9 风险表
