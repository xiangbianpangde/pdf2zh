# M3 压测报告

- 日期：2026-10-27 ~ 28（北京时间）
- 环境：macOS，Python 3.12（anaconda），同一台机器、同一网络
- 目的：验证批量、并发、两层缓存、失败隔离、翻译并发与限流
- 结论：**M3 门禁通过**（两项按设计不适用，见 §5）

## 1. 输入

| 文件 | 页数 | 大小 |
|---|---:|---:|
| `1-s2.0-S0141938225001878-main.pdf` | 16 | 5.2 MB |
| `attention_is_all_you_need.pdf` | 15 | 2.1 MB |
| `39148389_2024_CFD-16-children.pdf` | 5 | 1.0 MB |
| 本机 44 页中文论文（未入库，仅用于超限测试） | 44 | 2.0 MB |

累计消耗 MinerU 约 80 页（远低于 1000 页/天）。

## 2. 结果矩阵

| # | 场景 | 命令要点 | 结果 |
|---|---|---|---|
| A | flash 3 份并发 | `--ocr flash --parallelism 3` | **47.3 s**，3/3 成功，exit 0 |
| B | 完成标记复用 | 同 A 重跑 | **0.13 s**，3/3「已有有效交付」，**0 次 API** |
| B2 | OCR 缓存复用 | 删掉 `_meta/completion.json` 后重跑 | **0.13 s**，3/3「命中 OCR 缓存」，**0 次 MinerU** |
| C | 失败隔离 | 3 份 + 1 个 44 页文件，`--parallelism 4` | **112.0 s**，3 成功 1 失败，exit 1，失败者无交付目录 |
| C2 | flash 页数上限 | 44 页 | 11.4 s 失败，`err_msg` 指明 20 页上限并给出建议 |
| C3 | 建议可执行 | 44 页 + `--pages 1-20` | **66.8 s** 成功，40.8 KB Markdown，校验通过 |
| D1 | 翻译并发 1 | 7 块，`--workers 1` | **102.2 s**（14.6 s/块） |
| D2 | 翻译并发 8 | 7 块，`--workers 8` | **38.8 s**（5.5 s/块），**2.6×** |
| E | precision 2 份并发 | `--parallelism 2`（15 页 + 5 页） | **26.5 s**，图片 14 + 3，两份校验通过 |
| F1 | 限流探测（短请求） | 并发 8 / 16 / 32 | 全部 200，墙钟 3.6 / 4.2 / 4.9 s |
| F2 | 限流探测（3000 字） | 并发 4 / 8 / 16 | 全部 200，墙钟 30.0 / 37.3 / 33.6 s，**无 429** |
| G | 429 重试（确定性） | 本地 stub 先返回 429×2 | 第 3 次成功；耗尽重试后返回 429；400 不重试 |

D1/D2 两次运行的中文 Markdown 均 `validation.ok == true`，中英图片引用序列一致。

## 3. 关键发现

1. **两层复用都成立，且都能做到 0 次外部调用。**
   完成标记（`_meta/completion.json` 指纹）省掉全部工作，0.13 s 返回；删掉标记后，OCR 缓存（源 sha256 + 通道参数）仍然命中，同样 0.13 s、0 次 MinerU 调用。
   这解决的是「重跑一次就多花一次配额」的实际风险。

2. **失败隔离成立。** 4 份并发里 1 份因页数超限失败：其余 3 份正常出交付，进程退出码 1，失败者不产生交付目录，缓存里也不留空 run 目录。

3. **flash 的真实页数上限是 20 页**（官方文档示例里的 50 页是旧文案）。错误信息已带上可执行建议，`--pages 1-20` 实测可绕过并在 66.8 s 内产出可用 Markdown。

4. **该翻译端点没有可观测的并发限流。** 16 并发 × 3000 字仍全 200；吞吐随并发上升（等效 0.13 → 0.21 → 0.48 req/s），单请求延迟因排队从 16 s 涨到 33 s。默认 `--workers 8` 偏保守但不浪费。
   429 的重试/不重试分支由本地 stub 确定性覆盖，不依赖服务端状态。

5. **MinerU 队列耗时波动大**（同一文件 6–20 s）。因此「等待超时」与「解析失败」必须分开报——实现里已经分开，压测没有出现把超时误判为失败的情况。

## 4. 未覆盖 / 已知缺口

| 缺口 | 原因 | 影响 |
|---|---|---|
| 1000 页/天配额 | 需要 >1000 页样本才可能触发 | 未知的是「超额后队列变多慢」，不是会不会失败 |
| 单批 ≤50 文件 | 设计固定 1 文件/请求，不使用该上限 | 无 |
| 翻译真实中断续跑 | 只做了单元测试（检查点逐块落盘） | 端到端未验证，风险中等 |
| 8+ 文件同时并发 | 最多测到 4 并发 | 更高并发下的本地资源与限流未知 |

## 5. 门禁判定

| 门禁（ROADMAP M3） | 结果 |
|---|---|
| 3 份 PDF 并发成功 | ✅ 3/3（flash 与 precision 各测一次并发） |
| 缓存命中不重复调 API | ✅ 两层各测：0 次外部调用 |
| 配额内不触发限流 | ✅ 累计约 80 页；翻译端点 16 并发无 429 |

结论：**通过**。两个未覆盖项属于「超出设计范围」或「需要更大样本」，不阻塞 M4。

## 6. 复现方式

```bash
# 3 份并发 + 缓存复跑
python3 scripts/pdf2md.py /abs/a.pdf /abs/b.pdf /abs/c.pdf -o /tmp/m3/outA --ocr flash --parallelism 3 --json
python3 scripts/pdf2md.py /abs/a.pdf /abs/b.pdf /abs/c.pdf -o /tmp/m3/outA --ocr flash --parallelism 3 --json

# 失败隔离（混入超页文件）
python3 scripts/pdf2md.py /abs/a.pdf /abs/b.pdf /abs/c.pdf /abs/44p.pdf -o /tmp/m3/outC --ocr flash --parallelism 4 --json
# 绕过页数上限
python3 scripts/pdf2md.py /abs/44p.pdf -o /tmp/m3/outC3 --ocr flash --pages 1-20

# 翻译并发对比（先让 OCR 进缓存，再分别跑）
python3 scripts/pdf2md.py /abs/short.pdf -o /tmp/m3/outD1 --ocr flash --translate --workers 1
python3 scripts/pdf2md.py /abs/short.pdf -o /tmp/m3/outD2 --ocr flash --translate --workers 8

# 429 重试的确定性验证
cd pdf-to-md && python3 -m unittest tests.test_netclient -v
```
