# ROADMAP — PDF → Markdown Skill

> 阶段门驱动，不写虚构日期。**进入下一阶段的唯一条件是本阶段门禁全部通过。**

## 进度

| 阶段 | 状态 |
|---|---|
| M0 立项与底线 | ✅ 完成（2026-10-27） |
| M1 单通道跑通 | ✅ 完成（真实 PDF 实测） |
| M2 可选翻译 | ✅ 完成（真实模型实测） |
| M3 批量与缓存 | ✅ 完成（压测见 `docs/M3-STRESS.md`） |
| M4 Skill 收口 | ✅ 完成（已部署并真实调用） |
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

## M1 单通道跑通 ✅

范围：**只做 precision + 不翻译**。把交付契约钉死。

已落地：

- [x] `references/output-contract.md`：目录、命名、`images/` 引用规则、九类硬校验、`_meta/` 内容
- [x] `scripts/mineru_client.py`：`POST /api/v4/file-urls/batch` → 裸 `PUT` → 轮询批量结果 → 下载 zip（防 zip-slip）→ 解包
- [x] `scripts/organize.py`：按 md 引用定位图片 → 真实编码嗅探 → 复制进 `images/` → 重写链接 → 写 `manifest.json`
- [x] `scripts/validate.py`：缺图 / 外链 / 绝对路径与 `..` / 扩展名与 MIME 不符 / 不可解码 / 中英序列不一致 / 空 md / 非 UTF-8
- [x] `scripts/pdf2md.py` + `--doctor`：参数、退出码（0/1/2）、凭据自检（真实探测 MinerU 与模型端点）
- [x] `tests/`：45 项（图片归一化与冲突、引用扫描与改写、九类校验、分块边界、检查点续跑、CLI 退出码、失败路径）

门禁核验：

- 真实 PDF（16 页 / 5.2 MB）precision → 21 s，67 张图，`validation.json.ok == true` ✅
- 图片引用 57 条全部可解析，Pillow 实际解码 ✅
- 错误 Token → `HTTP 401「user token expired」`，退出码 1，不留交付目录 ✅

## M2 可选翻译 ✅

已落地：

- [x] `references/translation.md`：三个模型参数、端点拼接、网关兼容、分块、检查点、失败语义
- [x] `scripts/translate.py`：按空行分块（不在围栏/块级公式/表格内切）、并发、检查点、指纹失效
- [x] 结构保护：公式、代码块、图片引用、表格分隔符、URL 不译；`<think>` 块剥离
- [x] 模型可切换：`--base-url` / `--model` / `--api-key` / `--api-header`，优先级 命令行 > 环境变量 > `.secrets` > `~/.config/pdf2md` > `.env`
- [x] 测试：闭合定界符不关闭分块、跨行围栏不切开、检查点只恢复缺失块、上下文变化则失效

门禁核验：

- 同一份 PDF：不翻译得英文交付；翻译后中英图片序列 57/57 一致 ✅
- 标题 31/31、HTML 表格 7/7、块级公式 6/6 未变 ✅
- 翻译失败时 `_zh.md` 与 `completion.json` 都不写，英文与检查点保留（有回归用例） ✅

> 未做：中途 kill 后真实续跑的端到端验证（单元测试已覆盖检查点语义）。

## M3 批量与缓存 ✅

已落地（代码）：

- [x] 多 PDF 输入、`--parallelism`、每文件独立目录、`--json` 汇总
- [x] 缓存：`<out>/.cache/<sha256(源+参数)>/`，含 `receipt.json` + `READY`；失败时清理空目录
- [x] `--force` 忽略完成标记与缓存；同名不同内容拒绝覆盖（sha256 比对）
- [x] 测试：缓存命中不调 API、完成指纹不匹配则重做、失败不留半成品

压测结果（详见 `docs/M3-STRESS.md`）：

- [x] flash 3 份并发 47.3 s，3/3 成功；precision 2 份并发 26.5 s
- [x] 两层复用：完成标记 0.13 s / 0 次 API；删标记后 OCR 缓存仍 0 次 MinerU
- [x] 失败隔离：4 份里 1 份超页失败，其余正常，exit 1，失败者无交付目录
- [x] flash 页数上限实测 = 20 页（非文档示例的 50）；`--pages 1-20` 可绕过
- [x] 翻译并发 workers 1→8 提速 2.6×；端点 16 并发无 429；429 重试用本地 stub 确定性验证

已知缺口（不阻塞 M4）：

- [x] 翻译真实中断续跑：SIGKILL 实测，只补缺失块、已完成块 0 次重发（`tests/resume_smoke.py`）
- [x] 8 份并发：44.1 s vs 串行 269.5 s（6.1×），8/8 成功、无 429、无串档
- [ ] 1000 页/天配额未触发（累计约 100 页）
- [ ] 单批 ≤50 按设计不使用（固定 1 文件/请求）
- [ ] 可选：批量时镜像输入目录树（待用户确认）

## M4 Skill 收口 ✅

结果：

- [x] `SKILL.md` 定稿：触发边界（含 3 条反例）、通道判断、失败报告、报告格式、证据边界；description 544 字符
- [x] `references/troubleshooting.md`：五类现象 → 原因 → 处置（凭据 / 解析 / 校验 / 翻译 / 重跑）
- [x] `tests/trigger_check.py`：静态描述检查 8/8 + 路由模拟 6/6（正例 3、反例 3）
- [x] 部署：软链 `~/.pi/agent/skills/pdf-to-md` → 本项目（skills 根目录，已随合并移除 `task/` 旧链接）
- [x] Pi 自带 loader（`loadSkillsFromDir`）：task/ 下 8 个技能、`pdf-to-md` 在内、**诊断 0 条**
- [x] validator `--strict`：PASS，0 error 0 warning；`CHANGELOG.md` 有 0.3.0 段
- [x] 真实调用：`pi -p "/skill:pdf-to-md …"` 跑通，产物落地（`validation.ok=true`、flash 通道、无中文），与模型报告一致

未做：真实模型路由只验证了「显式`/skill:` 调用」，没有做无提示的自由路由统计（模型口头列举技能不稳定，不作为证据）。

## M5 扩展（不阻塞主线）

| 项 | 价值 | 前置 |
|---|---|---|
| `--html` 阅读预览 | 便于审阅 | M2 |
| `extra_formats`（docx/html/latex） | 直接要 Word 的用户 | M1 |
| flash 通道成熟化 | 无 Token 也能用 | M1 实测页数上限 |
| OCR/翻译 benchmark | 选型有据 | M3 |
| `pi-paper-translator` 兼容层 | 修好坏掉的 `pdf_translate` | 用户决定是否要 |

## 首次实跑的准备清单（已完成，留档）

1. ~~本 worktree 的 `.env` / `.secrets` 已按指令删除~~ → 已重建 `.secrets/pdf2md.json`（`.gitignore` 覆盖）
2. 样例 PDF：`~/Downloads/1-s2.0-S0141938225001878-main.pdf`
3. 输出根目录用 `/tmp/pdf2md-e2e`、`/tmp/pdf2md-flash`，不污染 home
4. 核对项：`_meta/validation.json`、`ls images | wc -l`、md 内 `images/` 引用计数

## 变更管理

- 契约（§5.2 目录结构、校验规则）变更必须先改 `docs/PLAN.md`，再改代码与测试
- 阶段门禁未过时不开新阶段；发现门禁不成立时，先记录到 PLAN §9 风险表
