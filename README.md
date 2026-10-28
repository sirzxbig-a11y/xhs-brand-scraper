# 小红书视频品牌刮削

本地自动化工具：抓取指定小红书博主**最新 30 条视频**，通过 **画面 OCR（DeepSeek 视觉）+ 口播 ASR（阿里云/腾讯云）+ LLM 品牌抽取归一**，产出「博主 → 品牌 → 视频条目」的品牌露出时间线。**全程不下载视频文件**，仅在后台静默播放时截屏与捕获数字音频流。

- 主运行方式：本地手动运行（常驻后端 + 浏览器页面）
- 可选增强：挂载到 GitHub Actions 定时运行（Cookie 注入）

---

## 1. 功能特性

| 能力 | 说明 |
|---|---|
| 博主管理 | 前端页新增/删除/编辑博主（昵称、小红书号必填，主页链接选填） |
| 视频收集 | 抓取最新发布的 **30 条视频**，严格跳过图文笔记 |
| 静默捕获 | 不下载视频；`<video>` 元素定时截屏 + Web Audio 静音重路由捕获数字音频流 |
| 画面 OCR | 调用 DeepSeek 视觉 API（OpenAI 兼容）识别画面品牌 |
| 口播 ASR | 页面下拉切换 阿里云（Paraformer）/ 腾讯云（一句话识别），含句级时间戳 |
| 品牌抽取归一 | 只保留标准品牌名；中英文别名归并；产品名/系列名不单列 |
| 时间对齐 | ±5s 精度；同一品牌按画面/口播分别列出多个时间段 |
| 登录态 | 首次扫码登录持久化，后续复用 |
| 风控处理 | 遇验证码中断当前博主、标记「遇到风控」、继续下一个，可手动重抓 |
| 定时任务 | 后端常驻时每天自动全量重抓（覆盖写结果） |

---

## 2. 目录结构

```
小红书视频品牌刮削/
├── run.py                     # 启动入口：python run.py
├── requirements.txt
├── README.md
├── .gitignore
├── .github/
│   └── workflows/
│       └── xhs-scrape.yml     # GitHub Actions 定时抓取（可选）
├── backend/                   # 本地后端（FastAPI）
│   ├── config.py              # 路径常量 + 数据模型 + 读写封装
│   ├── storage.py             # 原子 JSON 读写（线程安全）
│   ├── scheduler.py           # APScheduler 每日定时
│   ├── pipeline.py            # 编排：抓取→捕获→识别→落盘
│   └── main.py                # REST 路由 + 前端托管
├── scraper/                   # 浏览器自动化
│   ├── xhs_scraper.py         # 登录态 + 收集 30 条视频
│   └── capturer.py            # 静默截屏 + 音频流捕获 + 转码
├── recognizer/                # 识别层
│   ├── ocr.py                 # DeepSeek 视觉 OCR
│   ├── asr.py                 # 阿里/腾讯 ASR
│   └── brand.py               # LLM 品牌抽取归一 + 时间对齐
├── scripts/
│   ├── export_cookies.py      # 导出登录态 Cookie（供 Actions 用）
│   └── headless_run.py        # 无头批量抓取入口（供 Actions 用）
├── frontend/
│   ├── index.html
│   ├── app.js
│   └── style.css
└── data/
    ├── bloggers.json          # 博主配置
    ├── api_keys.json          # API Key（本地，不提交 git）
    ├── results/               # 每博主结果 JSON（博主→品牌→视频）
    └── tmp/                   # 截帧/音频中间产物
```

---

## 3. 环境要求

- **Python 3.10+**（推荐 3.12）
- **Chrome/Chromium**（由 Playwright 自动下载，无需手动装）
- **无需系统 ffmpeg**：音频转码使用 `imageio-ffmpeg` 内置静态 ffmpeg
- 网络可访问：小红书网页版、DeepSeek API、所选的 ASR 云厂商

---

## 4. 安装依赖

```bash
cd "小红书视频品牌刮削"

# 1) 安装 Python 依赖
pip install -r requirements.txt

# 2) 下载 Chromium（Playwright 会自动下载到用户目录）
python -m playwright install chromium
```

> Windows 若 `python` 不可用请用 `py -3`；安装慢可加国内镜像：
> `pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple`

---

## 5. 启动本地服务

```bash
python run.py              # 默认 http://127.0.0.1:8000
python run.py --port 8080  # 自定义端口
```

浏览器打开 **http://127.0.0.1:8000**，即可看到三标签页：博主管理 / API 配置 / 数据展示。

---

## 6. 首次扫码登录（重要）

1. 在「博主管理」新增一个博主（昵称 + 小红书号，主页链接选填）。
2. 点击该博主的 **「重新抓取」**。
3. 首次运行时后端会**自动弹出 Chrome 浏览器**（有头模式）跳转小红书，请用小红书 App 扫码登录。
4. 登录成功后，登录态会持久化到 `browser_profile/` 目录，之后抓取自动复用、不再弹窗。
5. 若 3 分钟未扫码会超时，博主状态置为「失败」，重新点「重新抓取」即可。

> 也可以手动先登录一次再抓取：直接运行
> `python -c "from scraper.xhs_scraper import *; import playwright.sync_api as pw; from backend import config; exec('')"` 略——更简单的方式是点一次「重新抓取」触发。

---

## 7. 配置 API Key

在页面「API 配置」标签填写并保存（保存后写入 `data/api_keys.json`）：

- **DeepSeek**：API Key、Base URL（默认 `https://api.deepseek.com`）、文本模型（默认 `deepseek-chat`，用于品牌抽取/归一）
- **画面 OCR**：默认走阿里云 DashScope 的 `qwen3-vl-plus`（DeepSeek 官方 API 无视觉模型），复用下面的 DashScope Key
- **ASR 服务商**：下拉选择 `阿里云` 或 `腾讯云`
  - 阿里云：填 DashScope API Key（`sk-...`）
  - 腾讯云：填 SecretId、SecretKey（AppId 选填）

> 若你的 DeepSeek 视觉模型名不同（如已上线的 VL 系列版本号），在页面「视觉模型」里改成对应名称即可，OCR 走的是 OpenAI 兼容 `/chat/completions` 接口。

---

## 8. 数据展示与结果结构

「数据展示」页严格按 **博主 → 品牌 → 视频条目** 展示。每个视频条目含：发布时间、视频网址、画面时间段列表、口播时间段列表；同一视频可出现在多个品牌下，且只展示该品牌对应来源的时间段。

结果文件 `data/results/{小红书号}.json` 结构：

```json
{
  "blogger_id": "5f0...",
  "nickname": "某博主",
  "scraped_at": "2026-10-27T22:00:00+08:00",
  "videos": [
    {
      "note_id": "abc123",
      "title": "视频标题",
      "published_at": "2026-10-25",
      "url": "https://www.xiaohongshu.com/explore/abc123",
      "brands": {
        "华为": {
          "visual": [[5, 8], [20, 24]],
          "asr": [[12, 15]]
        }
      }
    }
  ]
}
```

时间单位：秒（页面展示为 `mm:ss`），精度约 ±5s。

---

## 9. 每日定时任务

后端常驻时，`backend/scheduler.py` 每天 **03:00（Asia/Shanghai）** 自动全量重抓配置中所有未删除的博主，并**覆盖写**对应结果 JSON。

- 想改时间：编辑 `backend/scheduler.py` 顶部的 `DAILY_HOUR` / `DAILY_MINUTE`。
- 页面顶栏会显示「下次定时」时间。

---

## 10. 常见问题（FAQ）

**Q1：截屏是黑帧？**
视频 `element.screenshot()` 在少数无头环境会黑帧。处理：设置环境变量 `XHS_HEADED=1` 强制有头模式再抓取；或改用较新的 Chromium（`python -m playwright install chromium --force`）。

**Q2：提示「遇到风控」？**
小红书风控策略会变。可：降低抓取频率、换网络、等一段时间后点「重新抓取」；必要时设置 `XHS_HEADED=1` 手动过一次验证码再抓。

**Q3：抓不到视频 / 图文混进来？**
小红书 DOM 会改版，选择器集中在 `scraper/xhs_scraper.py` 顶部 `SELECTORS` 常量，按实际页面结构更新即可。

**Q4：音频没捕获到？**
部分 CDN 视频存在跨域限制导致 `createMediaElementSource`/`captureStream` 失败。此时会跳过音频、仅做画面 OCR（日志有 `无法捕获音频流` 提示）。可尝试 `XHS_HEADED=1`。

**Q5：DeepSeek 视觉模型报错？**
确认「视觉模型」名与账号可用模型一致，且该 Base URL 支持视觉 `image_url` 输入。

**Q6：截屏/录音不完整怎么办？**
默认仍是「不下载视频」的截屏 + 音频流方案；一旦截屏或音频任一没拿到，会自动回退到「嗅探媒体地址 → 临时下载 → ffmpeg 抽帧 + 抽 16k 音频」，处理完自动删除临时视频。全程无需手动切换。

---

## 10.5 使用真实 Chrome 抓取（降低风控，可选）

Playwright 启动的独立 Chromium 没有浏览历史、指纹特征明显，容易被小红书识别。如果你日常的 Chrome 能正常访问小红书，可让本工具**直接连接你真实的 Chrome**（CDP 远程调试），复用真实登录态与浏览器指纹。

### 步骤

1. **启动带调试端口的 Chrome**：

   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\start_chrome_debug.ps1
   ```

   - 默认用独立配置目录 `%LOCALAPPDATA%\XhsScraperChrome`，不影响日常 Chrome；
   - 想用日常配置（指纹最真实）：先完全退出 Chrome，再加 `-UseMainProfile`。

2. **在这个 Chrome 窗口里登录小红书**（扫码/验证码都在这里完成）。

3. **带环境变量重启后端**：

   ```powershell
   $env:XHS_CDP="http://127.0.0.1:9222"
   python run.py
   ```

4. 打开 http://127.0.0.1:8000 → 「博主管理」→ 点 **「打开浏览器登录」**，检测到已登录后会自动开始抓取。

> 注意：CDP 只是降低「脚本指纹」被识别的概率；若提示 `IP存在风险 300012`，那是网络出口 IP 被拦，仍需换网络（热点/代理）。

### 相关环境变量速查

| 环境变量 | 作用 |
|---|---|
| `XHS_CDP` | 连接真实 Chrome 的调试地址，如 `http://127.0.0.1:9222` |
| `XHS_PROXY` | 代理地址，如 `http://127.0.0.1:7890`（换出口 IP） |
| `XHS_HEADED` | 设为 `1` 强制有头模式（排查黑帧/风控） |

---

## 11. （可选）挂载到 GitHub Actions 定时运行

见下一节。核心思路：本地导出登录态 Cookie → 存为 GitHub Secret → Actions 里无头抓取 → 结果提交回仓库。

### 11.1 导出 Cookie

本地先完成一次扫码登录，然后：

```bash
python scripts/export_cookies.py
```

脚本会打印一串 JSON，形如：

```json
[{"name":"web_session","value":"...","domain":".xiaohongshu.com","path":"/"}, ...]
```

把整串 JSON 存为 GitHub 仓库 Secret，名称为 **`XHS_COOKIE`**。

### 11.2 配置 GitHub Secrets

在仓库 `Settings → Secrets and variables → Actions` 新增以下 Secrets：

| Secret 名 | 内容 |
|---|---|
| `XHS_COOKIE` | 上一步导出的 Cookie JSON |
| `DEEPSEEK_API_KEY` | DeepSeek API Key |
| `DEEPSEEK_BASE_URL` | 默认 `https://api.deepseek.com` |
| `VISION_MODEL` | 视觉模型名，如 `deepseek-vl2` |
| `LLM_MODEL` | 文本模型名，如 `deepseek-chat` |
| `ASR_PROVIDER` | `aliyun` 或 `tencent` |
| `ALIYUN_APP_KEY` | 阿里云 DashScope Key（选阿里时） |
| `TENCENT_SECRET_ID` | 腾讯云 SecretId（选腾讯时） |
| `TENCENT_SECRET_KEY` | 腾讯云 SecretKey（选腾讯时） |

### 11.3 工作流说明

`.github/workflows/xhs-scrape.yml` 默认每天 **UTC 18:30（= 北京 02:30）** 运行，也支持手动 `Run workflow`。抓取结果会 `git push` 回仓库的 `data/results/`。

**注意事项：**

1. **Cookie 时效**：小红书 `web_session` 会过期，失效后需本地重新扫码并更新 `XHS_COOKIE` Secret。
2. **风控更易触发**：数据中心 IP（GitHub Actions）比本地 IP 更容易触发验证码，属预期；可降低频率、或改用自托管 runner。
3. **提交权限**：工作流已设置 `permissions: contents: write`，用 `GITHUB_TOKEN` 即可推送；若仓库有分支保护，需额外配置。
4. **成本**：OCR/ASR/LLM 均按调用计费，30 条视频逐条识别会产生 API 调用量，请留意账单。
5. **不提交密钥**：`data/api_keys.json` 与 `browser_profile/` 已在 `.gitignore` 中排除，切勿把真实 Key 提交进仓库。

---

## 12. 免责声明

本项目仅用于个人学习与合规的品牌露出研究。请遵守小红书用户协议、robots 规则及相关法律法规，控制抓取频率，勿用于商业用途或对平台造成压力。
