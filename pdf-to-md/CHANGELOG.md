# Changelog

## 0.1.0 - 2026-10-27

- M0 立项：建立 skill 包骨架（`SKILL.md`、`references/`、`CHANGELOG.md`）与仓库级 `docs/`。
- 新增 `references/mineru-api.md`：从 <https://mineru.net/apiManage/docs> 抓取并整理的 MinerU 文档解析 API 参考，含两套 API 对比、端点与参数表、任务状态机、结果包成员、错误码、语言取值、对本项目的结论与待实测项。
- 记录三处官方文档与实现相关的硬约束：图片只在精准解析的 zip 内、Agent 接口已改为 JSON + 签名上传、轻量接口页数上限自相矛盾。
- `SKILL.md` 落定触发边界、通道判断、交付契约、失败报告规则；标注 `scripts/` 未实现，本包暂不可安装。
