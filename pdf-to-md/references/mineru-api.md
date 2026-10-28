# MinerU 文档解析 API（整理稿）

- 来源：<https://mineru.net/apiManage/docs>（页面标题「MinerU 文档解析接口文档」）
- 抓取时间：2026-10-27，抓取方式：渲染后取页面正文，共 34,158 字符
- 本文只做**结构重排与表格化**，不新增官方未写的内容。凡标 `⚠️` 的是官方页面自身不一致、或对实现有硬约束的点。

## 1. 两种 API 一句话对比

| 对比维度 | 精准解析 API | Agent 轻量解析 API |
|---|---|---|
| 是否需要 Token | ✅ 需要（API 管理页创建） | ❌ 无需，IP 限频 |
| 接口基址 | `/api/v4/extract/task`、`/api/v4/file-urls/batch` | `/api/v1/agent/parse/url`、`/api/v1/agent/parse/file` |
| 模型版本 | `pipeline`（默认）/ `vlm`（推荐）/ `MinerU-HTML` | 固定 pipeline 轻量模型 |
| 文件大小 | ≤ 200 MB | ≤ 10 MB |
| 页数 | ≤ 200 页 | ≤ 20 页 ⚠️（见 §6.1） |
| 批量 | ✅ 支持（单次链接 ≤ 50 个） | ❌ 单文件 |
| 输出 | Zip：Markdown + JSON，可加 docx/html/latex | 仅 Markdown（CDN 链接） |
| 调用方式 | 异步：提交 → 轮询 | 异步：提交 → 轮询 |

**对「保留图片」这条要求，只有精准解析 API 可用**：轻量接口只返回一个 Markdown 链接，没有 zip、没有 `images/`。

## 2. 精准解析 API（v4，需要 Token）

基址 `https://mineru.net/api/v4`。所有请求需带：

```
Content-Type: application/json
Authorization: Bearer <token>
```

### 2.1 文件限制

| 限制项 | 值 |
|---|---|
| 文件大小 | ≤ 200 MB |
| 页数 | ≤ 200 页 |
| 文件类型 | PDF、图片（png/jpg/jpeg/jp2/webp/gif/bmp）、doc/docx、ppt/pptx、xls/xlsx、html |
| 配额 | 每账号每天 1000 页最高优先级；超出部分优先级降低 |
| 网络 | github、aws 等国外 URL 会超时（走 URL 模式时注意） |

### 2.2 四个入口

| 场景 | 方法与路径 | 返回 |
|---|---|---|
| 远程 URL 单文件 | `POST /extract/task` | `data.task_id` |
| 本地文件（单/批量，≤50） | `POST /file-urls/batch` → 逐个 `PUT` 到 `file_urls[i]` | `data.batch_id` + `data.file_urls[]` |
| 远程 URL 批量 | `POST /extract/task/batch` | `data.batch_id` |
| 查询单任务 | `GET /extract/task/{task_id}` | `data.state` / `data.full_zip_url` |
| 查询批量 | `GET /extract-results/batch/{batch_id}` | `data.extract_result[]` |

本地文件流程的三条硬约束：

1. 上传链接**有效期 24 小时**，需在有效期内完成上传。
2. `PUT` 上传时**不要设置 `Content-Type`**（旧实现踩过 403：签名与额外头不匹配）。
3. 上传完成后**不需要**再调提交接口，服务端自动扫描并提交解析。

### 2.3 请求参数（单文件 `POST /extract/task`）

| 参数 | 类型 | 必选 | 默认 | 说明 |
|---|---|---|---|---|
| `url` | string | 是 | — | 文件 URL，支持的类型见 §2.1 |
| `model_version` | string | 否 | `pipeline` | `pipeline` / `vlm` / `MinerU-HTML`；HTML 源文件必须显式用 `MinerU-HTML` |
| `is_ocr` | bool | 否 | `false` | 仅 pipeline / vlm |
| `enable_formula` | bool | 否 | `true` | 仅 pipeline / vlm；vlm 下只影响行内公式 |
| `enable_table` | bool | 否 | `true` | 仅 pipeline / vlm |
| `language` | string | 否 | `ch` | 见 §4 |
| `data_id` | string | 否 | — | 业务侧标识，≤128 字符，`[A-Za-z0-9_.-]` |
| `page_ranges` | string | 否 | — | 逗号语法，见 §2.6 |
| `extra_formats` | string[] | 否 | — | 只接受 `docx`、`html`、`latex`；markdown/json 默认就有；对 html 源文件无效 |
| `no_cache` | bool | 否 | `false` | 绕过 URL 内容缓存 |
| `cache_tolerance` | int | 否 | `900` | URL 缓存容忍秒数，`no_cache=false` 时生效 |
| `callback` | string | 否 | — | 结果回调地址；不填则必须轮询 |
| `seed` | string | 否 | — | 回调签名用；**使用 callback 时必填** |

`callback` 契约：服务端 POST JSON `{checksum, content}`；`checksum = SHA256(uid + seed + content)`；接收方返回 HTTP 200 视为成功，失败最多重推 5 次。

### 2.4 本地批量上传（`POST /file-urls/batch`）

```json
{
  "files": [{"name": "demo.pdf", "data_id": "abcd", "is_ocr": false, "page_ranges": "1-20"}],
  "model_version": "vlm",
  "language": "en",
  "enable_formula": true,
  "enable_table": true,
  "extra_formats": ["docx"]
}
```

| 参数 | 说明 |
|---|---|
| `file.name` | 必填，**强烈建议带正确后缀**，服务端据此判断类型 |
| `file.is_ocr` / `file.page_ranges` / `file.data_id` | 逐文件设置 |
| `callback` / `seed` / `extra_formats` / `model_version` / `language` / `enable_formula` / `enable_table` | 整批共享 |
| 批量上限 | 单次申请链接 ≤ 50 个 |

响应：`{"code":0,"data":{"batch_id":"...","file_urls":["https://..."]}}`，`file_urls` 与 `files` 顺序对应。

### 2.5 URL 批量（`POST /extract/task/batch`）

`files[]` 用 `{"url": ..., "data_id": ..., "is_ocr": ..., "page_ranges": ...}`；另外支持整批 `no_cache` / `cache_tolerance`。返回 `batch_id`。

### 2.6 页码范围语法（precision）

逗号分隔字符串，例如 `"2,4-6"` 表示第 2、4、5、6 页；`"2--2"` 表示从第 2 页到倒数第 2 页（`-2` 为倒数第二页）。页码以 1 为基。

### 2.7 任务状态

| state | 含义 | 出现位置 |
|---|---|---|
| `pending` | 排队中 | 单任务 / 批量 |
| `running` | 解析中（带 `extract_progress`） | 单任务 / 批量 |
| `converting` | 格式转换中 | 单任务 / 批量 |
| `waiting-file` | 等待文件上传（尚未提交解析） | 仅批量 |
| `done` | 完成，返回 `full_zip_url` | 单任务 / 批量 |
| `failed` | 失败，返回 `err_msg` | 单任务 / 批量 |

`running` 时附带：`extract_progress.extracted_pages`、`total_pages`、`start_time`。

### 2.8 结果包内容

`full_zip_url` 下载得到 zip，官方列出的成员：

| 文件 | 内容 |
|---|---|
| `full.md` | Markdown 解析结果 |
| `layout.json` | 中间处理结果（等价 middle.json） |
| `*_model.json` | 模型推理结果 |
| `*_content_list.json` | 内容列表（块级结构） |
| `images/` | 图片资源 ⚠️（命名随版本变化，见 §6.3） |
| `extra_formats` 对应文件 | docx / html / latex（若请求） |

HTML 源文件的输出略有不同：`full.md` 为正文 Markdown，`main.html` 为提取后正文 HTML。

官方对结果文件的详细说明：<https://opendatalab.github.io/MinerU/reference/output_files/>。⚠️ 该页描述的是 4.0 文档模型 / 自托管 V1 的契约（例如图片命名 `page_{page_idx}_{parent_type}_{parent_index}.{ext}`），与云 API v4 的 zip 成员**不保证一致**，不要混用两套契约。

### 2.9 错误码

| 错误码 | 说明 | 处理建议 |
|---|---|---|
| `A0202` | Token 错误 | 检查 Token 与 `Bearer` 前缀 |
| `A0211` | Token 过期 | 换新 Token |
| `-500` | 传参错误 | 检查参数类型与 `Content-Type` |
| `-10001` | 服务异常 | 稍后重试 |
| `-10002` | 请求参数错误 | 检查参数格式 |
| `-60001` | 生成上传 URL 失败 | 稍后重试 |
| `-60002` | 获取匹配的文件格式失败 | 文件名/链接需带正确后缀 |
| `-60003` | 文件读取失败 | 文件损坏，重新上传 |
| `-60004` | 空文件 | 上传有效文件 |
| `-60005` | 文件大小超限 | ≤ 200 MB |
| `-60006` | 文件页数超限 | 拆分或指定 `page_ranges` |
| `-60007` | 模型服务暂不可用 | 稍后重试 |
| `-60008` | 文件读取超时 | 检查 URL 可访问 |
| `-60009` | 任务提交队列已满 | 稍后重试 |
| `-60010` | 解析失败 | 稍后重试 |
| `-60011` | 获取有效文件失败 | 确认文件已上传 |
| `-60012` | 找不到任务 | 检查 `task_id` 是否有效 |
| `-60013` | 没有权限访问该任务 | 只能访问自己提交的任务 |
| `-60014` | 删除运行中的任务 | 运行中的任务不支持删除 |
| `-60015` | 文件转换失败 | 可先手动转 PDF |
| `-60016` | 文件转换失败 | 换导出格式或重试 |
| `-60017` | 重试次数达到上限 | 等模型升级后重试 |
| `-60018` | 每日解析任务数量已达上限 | 次日再试 |
| `-60019` | HTML 文件解析额度不足 | 次日再试 |
| `-60020` | 文件拆分失败 | 稍后重试 |
| `-60021` | 读取文件页数失败 | 稍后重试 |
| `-60022` | 网页读取失败 | 可能网络问题或限频 |

## 3. Agent 轻量解析 API（v1，免 Token）

基址 `https://mineru.net/api/v1/agent`。**无需任何认证头。**

| 限制项 | 值 |
|---|---|
| 文件大小 | ≤ 10 MB |
| 页数 | ≤ 20 页 ⚠️ |
| 文件类型 | PDF、图片（png/jpg/jpeg/jp2/webp/gif/bmp）、**docx、pptx、xlsx**（不含 .doc / .ppt / .html） |
| 批量 | 不支持，每次一个文件 |
| 限频 | 每 IP 每分钟请求数受限，超出返回 HTTP 429 |
| 输出 | 仅 Markdown CDN 链接 |

### 3.1 URL 解析

`POST /api/v1/agent/parse/url`，`Content-Type: application/json`（**不接受 multipart**）：

| 参数 | 必选 | 说明 |
|---|---|---|
| `url` | 是 | 远程文件 URL；不支持 HTML |
| `file_name` | 否 | 含扩展名，用于判断类型；不填则从 URL 推断 |
| `language` | 否 | 默认 `ch`，仅对 PDF 生效 |
| `enable_table` | 否 | 默认 `true`，仅 PDF |
| `is_ocr` | 否 | 默认 `false`，仅 PDF |
| `enable_formula` | 否 | 默认 `true`，仅 PDF |
| `page_range` | 否 | 仅 PDF；只支持 `from-to`（如 `1-10`）或单页（如 `5`），**不支持逗号语法** |

返回 `{"code":0,"data":{"task_id":"..."}}`。

### 3.2 本地文件签名上传

1. `POST /api/v1/agent/parse/file`，body 为 JSON：`{file_name, language, enable_table, is_ocr, enable_formula, page_range}`
2. 返回 `data.task_id` + `data.file_url`（OSS 签名地址）
3. `PUT` 文件字节到 `file_url`
4. 服务端自动开始解析，轮询查询结果

⚠️ 早期实现把文件用 multipart 直接 POST 到这个接口，**与当前契约不符**；现行契约是「先取签名 URL，再 PUT」。

### 3.3 查询结果

`GET /api/v1/agent/parse/{task_id}`：

| 字段 | 说明 |
|---|---|
| `data.state` | `waiting-file`（等待上传）/ `uploading`（下载中）/ `pending`（排队）/ `running`（解析中）/ `done` / `failed` |
| `data.markdown_url` | `state=done` 时的 Markdown CDN 链接 |
| `data.err_msg` / `data.err_code` | `state=failed` 时的原因 |

### 3.4 错误码

| 错误码 | 说明 | 应对 |
|---|---|---|
| `-30001` | 文件大小超出 10 MB | 改用精准解析或压缩/拆分 |
| `-30002` | 不支持该文件类型 | PDF/图片/Docx/PPTx/Xlsx |
| `-30003` | 页数超出轻量接口限制 | 改用精准解析或指定 `page_range` |
| `-30004` | 请求参数错误 | 检查必填参数 |

## 4. `language` 取值

默认 `ch`。

| 值 | 覆盖语言 | 说明 |
|---|---|---|
| `ch` | 中、英、繁中 | 默认 |
| `ch_server` | 中、英、繁中、日 | 繁体、手写体 |
| `en` | 英文 | 纯英文 |
| `japan` | 中、英、繁中、日 | 日文为主 |
| `korean` | 韩文、英文 | |
| `chinese_cht` | 中、英、繁中、日 | 繁体为主 |
| `ta` / `te` / `ka` / `el` / `th` | 泰米尔 / 泰卢固 / 卡纳达 / 希腊 / 泰文 + 英文 | |
| `latin` | 拉丁语系（法、德、西、葡、意等 40+） | 语系包 |
| `arabic` | 阿拉伯语系 | 语系包 |
| `cyrillic` | 西里尔语系 | 语系包 |
| `east_slavic` | 东斯拉夫语系 | 语系包 |
| `devanagari` | 天城文语系 | 语系包 |

## 5. 对本项目的直接结论

1. **「保留图片」= 必须走精准解析 API**；轻量接口只给 Markdown。
2. 无 Token 时只能做纯文本 Markdown，且必须显式告知用户图片缺失、结构信息（json）缺失。
3. 图片不能按 MinerU 的文件名去找（命名会随版本变），应以 **Markdown 里的图片引用**为准，解析后统一复制进 `images/` 并重写链接。
4. 单文件上传的 `PUT` 不要带 `Content-Type`；上传链接 24 小时内必须用完。
5. 批量上传单次 ≤ 50 个文件；账号每天 1000 页高优先级额度。
6. 页码范围两套语法：precision 用逗号（支持 `2--2`），flash 只支持 `from-to`/单页。
7. 轮询比 callback 更适合本机场景（callback 需要公网可达地址）。
8. 解包要防 zip-slip（校验每个成员解出的路径都在目标目录内）。

## 6. 待实测/存疑（不要当事实用）

1. ⚠️ **轻量接口页数上限：实测为 20 页**。限制表写 ≤ 20 页，而 `failed` 示例的 `err_msg` 写 "exceeds lightweight API limit (50 pages)"，属于旧文案。44 页文件实测返回：`file page count exceeds API limit (20 pages), please input page_range to specify the page range`。绕过方式：传 `page_range`（本项目 `--pages`）。
2. 本页有「限流策略」导航项，但正文未展开并发上限、每分钟请求数具体值。
3. 结果文件在服务端的保留时长、`full_zip_url` 的有效期未写明。
4. 账号 1000 页/天是「最高优先级额度」，超出后的实际影响（队列时长）未量化。
5. 云 API v4 的 zip 图片命名与 4.0 文档模型（`page_{idx}_...`）是否一致未确认——实现上应避免依赖。

## 7. 实测记录（2026-10-27）

环境：macOS，Python 3.12，同一台机器与网络。

| 场景 | 输入 | 实测 |
|---|---|---|
| precision 单文件 | 16 页 / 5.2 MB | 21 s（提交→解包），67 张图 |
| precision 2 并发 | 15 页 + 5 页 | 26.5 s，图片 14 + 3 张，均校验通过 |
| flash 单文件 | 16 页 / 5.2 MB | 22 s，结构完整（31 标题 / 7 表格 / 6 公式），0 张图 |
| flash 3 并发 | 16 + 15 + 5 页 | 47.3 s，3/3 成功，未触发 IP 限频 |
| flash 超页 | 44 页 | `failed`，上限 20 页（见 §6.1） |
| 配额 | 本轮累计约 60 页 | 远低于 1000 页/天，未触发降级 |

结论：异步提交 + 轮询适合本机场景；队列耗时波动大（同一文件 6–20 s 不等），因此超时与解析失败必须分开报。

## 8. 与旧实现的行为差异（迁移时注意）

| 事项 | 旧实现 | 现状 |
|---|---|---|
| flash 上传 | multipart 直接 POST 到 `/parse/file` | JSON 取签名 URL + 裸 `PUT` + 轮询 |
| 单/批量任务 | 总是走 `/file-urls/batch` | 相同；本项目固定 1 文件/请求，不依赖 50 个上限 |
| 错误信息 | 只打印 `code`/`msg` | `code` + `msg` + HTTP 状态 + 修复提示 |
