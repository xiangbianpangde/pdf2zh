# pdf2zh 使用说明（一页纸版）

把英文 PDF 论文转成中文 Markdown 的小工具。**双击 `pdf2zh.bat` 就能用**。

## 系统要求

- **Windows 10 / 11**
- **Python 3.10、3.11 或 3.12**（3.13 不行）
  - 没装的话去 https://www.python.org/downloads/ 下载
  - **安装时一定要勾**「Add Python to PATH」
- 可选：**NVIDIA 显卡 + ≥4 GB 显存**（CPU 也能跑，慢 5-10 倍）
- 可选：**DeepSeek API key**（在 https://platform.deepseek.com 注册获取）
  - 不想用 DeepSeek？可以在 UI 里切到 Ollama 或 LM Studio 本地模型

## 安装与启动

1. 拿到 `pdf2zh` 文件夹（解压你收到的 zip）
2. **双击文件夹里的 `pdf2zh.bat`**
3. 首次启动会自动完成：
   - 创建 `.venv\`（虚拟环境）
   - 安装依赖（**5-10 分钟，下载约 7 GB**，请耐心等）
   - 检测到 NVIDIA 显卡的话，问你装不装 CUDA 版 PyTorch（**建议 Y**）
   - 问你的 DeepSeek API key（粘上去回车，保存到本地 `.env`）
4. 然后浏览器会自动弹出 http://127.0.0.1:7860 — 这就是主界面

之后每次双击 `pdf2zh.bat` 都是**几秒钟开浏览器**，不再下载。

## 怎么用界面

1. **左边「PDF 文件」**：拖入一个或多个英文 PDF
2. **保持默认勾选**：`PDF → Markdown` + `Markdown → 中文` + `复用 mineru-api 常驻服务`
3. **点 ▶ 开始批量处理**
4. **右边「日志」**：实时进度
5. **右边「输出文件」**：每完成一个就出现下载链接；点 📂 按钮打开输出文件夹

输出位置（每个 PDF 一个子文件夹）：
```
pdf2zh\webui_output\<论文名>\auto\
    <论文名>.md       ← 英文 markdown
    <论文名>_zh.md    ← 中文 markdown（这是你要的）
    images\           ← 抽取的图片
```

## 关闭服务

直接关掉黑色命令行窗口即可。

## 切换本地模型（Ollama / LM Studio）

不想用 DeepSeek API 的话：

1. 装 [Ollama](https://ollama.com)，命令行跑 `ollama pull qwen2.5:14b`
2. 在 UI 的「翻译模型设置」面板里，「翻译后端预设」选 **Ollama 本地**
3. 自动填好 Base URL 和模型名，直接开始

LM Studio 同理：先在 LM Studio Developer 页签启动 Local Server，再在 UI 里选「LM Studio 本地」。

## 常见问题

| 现象 | 怎么办 |
|---|---|
| `[ERROR] Python not found` | 没装 Python 或安装时没勾「Add to PATH」。重装 Python 并勾选 |
| `[ERROR] Python 3.13 is not supported` | 装一个 3.10/3.11/3.12 版本，可以多版本共存，bat 会用 `python` 命令找到的那个 |
| 首次 `pip install` 卡在某个包 | 网络问题。删掉 `.venv\` 重新双击；或挂代理重试 |
| GPU 跑着跑着显存炸 | UI 里把后端从 hybrid/vlm 切回 `pipeline`（默认就是它） |
| 翻译特别慢 | UI「翻译并发数」调大（DeepSeek 8 左右，本地模型按显存调） |
| 想停止当前批处理 | 直接关命令行窗口；下次启动会跳过已经完成的 PDF |

## 文件清单（参考）

- `pdf2zh.bat` — 双击这个
- `app.py` — Gradio 主程序（bat 自动调）
- `pdf_to_md.py` / `translate_md.py` / `pdf_to_zh_md.py` — 命令行单功能脚本
- `tools/test_batch_via_ui.py` — 通过 HTTP 自动化驱动 UI 的脚本（高级）
- `requirements.txt` / `README.md` / `LICENSE` — 项目元信息
- `.venv\` — 虚拟环境（首次启动自动建，不要手动删）
- `.env` — 你的 API key（首次启动询问后保存，**不要提交到 git**）
- `webui_output\` — 输出目录（含日志和 markdown）
