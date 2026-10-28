# Changelog

## 0.3.2 - 2026-10-28

- 修复可移植性缺陷（CI 发现）：未安装 Pillow 时，**截断的图片会被误判为有效**——旧逻辑只看文件头就放行。现在无 Pillow 时退化为容器完整性检查（PNG 的 IHDR/IDAT/IEND、JPEG 的 SOI/EOI、GIF trailer、WEBP/BMP 声明长度、TIFF IFD 偏移），`verified_by` 记为 `structure`；两条路径都会拒绝截断文件。
- 新增 3 项用例固定该行为（在屏蔽 Pillow 的环境下运行，等价于 CI）；离线用例 54 → 57。
- 恢复 GitHub Actions：从 `pdf-to-md/` 跑 compileall、57 项离线测试、真实中断续跑、退出码契约（无凭据 doctor → 1，用法错误 → 2）。

## 0.3.1 - 2026-10-28

- 真实中断续跑验证：新增 `tests/resume_smoke.py`，用真实 SIGKILL 杀掉翻译中的 CLI 子进程后重跑同一命令——续跑请求数等于缺失块数，**已完成块 0 次重发**，在途块重发上限为 `--workers`（该边界已写入 `references/translation.md`）。
- 8 份并发压测：44.1 s vs 串行 269.5 s（6.1×），8/8 成功、无 429、峰值 RSS 96.9 MB，8 份 `manifest.source.sha256` 与输入逐一匹配（无串档）。
- `docs/M3-STRESS.md` 补齐这两节的命令与数据；`docs/ROADMAP.md` 对应缺口关闭。

## 0.3.0 - 2026-10-28

- M4 收口：新增 `references/troubleshooting.md`（凭据 / 解析 / 校验 / 翻译 / 重跑五类现象 → 原因 → 处置）。
- `SKILL.md` 增加「报告格式」（成功三行、失败一句原因 + 影响 + 下一步），并指向 troubleshooting。
- 新增 `tests/trigger_check.py`：静态描述检查 + 路由模拟（3 正例 / 3 反例），需要 `--confirm` 才发起真实模型请求。
- 部署到 `~/.pi/agent/skills/task/pdf-to-md`（软链到本项目），Pi 自带 loader 检测到该技能、诊断数为 0。
- 触发验证：静态检查 8/8 通过；路由模拟 6/6 命中（正例 3、反例 3）。

## 0.2.1 - 2026-10-28

- M3 压测（`docs/M3-STRESS.md`）：flash 3 份并发 47.3 s；precision 2 份并发 26.5 s；失败隔离成立（4 份中 1 份超页失败，其余正常，失败者无交付目录）。
- 实测两层复用均为 0 次外部调用：完成标记 0.13 s 跳过；删掉标记后 OCR 缓存仍命中、不调 MinerU。
- 实测 flash 真实页数上限为 20 页（官方示例的 50 页是旧文案）；失败信息新增「用 `--pages 1-20` 或改 `--ocr precision`」提示，44 页文件传 `--pages 1-20` 实测成功。
- 翻译并发：workers 1→8 提速 2.6×（7 块 102.2 s → 38.8 s）；该端点 16 并发 × 3000 字无 429。
- 新增 `tests/test_netclient.py`：用本地 stub 确定性验证 429 重试、400 不重试、User-Agent 固定、空响应与 `<think>` 剥离、端点 URL 拼接。

## 0.2.0 - 2026-10-27

- 实现可执行管线：`scripts/pdf2md.py` 编排、`mineru_client.py` 双通道、`organize.py` 归位、`validate.py` 硬校验、`translate.py` 可选翻译、`config.py` 凭据解析、`netclient.py` 网络层、`images.py` 图片归一化、`mdlinks.py` 引用扫描、`errors.py` 失败分类。
- 模型可切换：`--base-url` / `--model` / `--api-key`（含 `--api-header`），优先级为 命令行 > 环境变量 > `.secrets` > `~/.config/pdf2md` > `.env`；`--doctor` 打印每个值的来源并做真实连通性探测。
- precision 通道按官方文档实现：`POST /api/v4/file-urls/batch` → 裸 `PUT` → 轮询批量结果 → 下载 zip（含 zip-slip 防护）→ 解包。
- flash 通道按官方**新**契约实现：`POST /api/v1/agent/parse/file`（JSON）取签名 URL → `PUT` → 轮询 → 下载 Markdown；旧实现使用的 multipart 直传已不适用。
- 实测记录：真实 PDF（16 页 / 5.2 MB）precision 解析 21s、67 张图、0 issue；flash 22s、0 图、结构完整；`deepseek-v4.1-flash` 26 块 6 并发翻译 122s，中英标题 31/31、HTML 表格 7/7、块级公式 6/6、图片序列 57/57 一致。
- 网关兼容：固定 `User-Agent`（默认 urllib UA 会被 Cloudflare 以 `1010` 拦下）；host 含 `opencode.ai` 时自动附加 `x-opencode-session`。
- 失败不留半成品：OCR 失败清理空运行目录；中文文件先写临时文件、校验通过后原子替换；翻译失败保留英文与检查点。
- 新增 `references/output-contract.md`（九类校验码、命名规则、缓存与重跑）与 `references/translation.md`（参数、端点拼接、分块、检查点、失败语义）。
- 测试：45 项离线用例覆盖图片归一化/冲突、引用扫描与改写、九类校验、分块边界、检查点续跑、CLI 退出码与失败路径。

## 0.1.0 - 2026-10-27

- M0 立项：建立 skill 包骨架（`SKILL.md`、`references/`、`CHANGELOG.md`）与仓库级 `docs/`。
- 新增 `references/mineru-api.md`：从 <https://mineru.net/apiManage/docs> 抓取并整理的 MinerU 文档解析 API 参考，含两套 API 对比、端点与参数表、任务状态机、结果包成员、错误码、语言取值、对本项目的结论与待实测项。
- 记录三处官方文档与实现相关的硬约束：图片只在精准解析的 zip 内、Agent 接口已改为 JSON + 签名上传、轻量接口页数上限自相矛盾。
- `SKILL.md` 落定触发边界、通道判断、交付契约、失败报告规则；标注 `scripts/` 未实现，本包暂不可安装。
