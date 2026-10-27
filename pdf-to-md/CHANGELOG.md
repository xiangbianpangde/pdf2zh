# Changelog

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
