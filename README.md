# PPT Master 生成服务 · 部署指南

把 ppt-master 的 SVG→PPTX 转换能力放到公网，供网站调用。

**架构**

```
浏览器 → Cloudflare Worker（/api/ppt-build）
             → Render 上的本服务（FastAPI + ppt-master）
                  ├─ 调 DeepSeek 写每页 SVG
                  ├─ 跑 svg_quality_checker 质检
                  ├─ 失败自动修复（最多 2 轮）
                  └─ 跑 svg_to_pptx 转成原生 PPTX
             ← 返回 .pptx 二进制
```

---

## 一、本地验证（可选，先确认能跑）

```bash
cd ppt-service
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt

# 配置 Key
set DEEPSEEK_API_KEY=sk-xxxx

# 启动
uvicorn app:app --port 8000
```

打开 <http://localhost:8000/api/health> 应返回 `{"ok":true,"has_key":true,...}`。

---

## 二、部署到 Render（免费层）

### 1. 把服务推到 GitHub

Render 需要从 Git 仓库拉代码。新建一个仓库，把 `ppt-service/` 目录推上去：

```bash
cd ppt-service
git init
git add .
git commit -m "pptx service"
git remote add origin https://github.com/你的账号/ppt-service.git
git push -u origin main
```

> ⚠️ `skill/` 目录有 400 个文件、9.9 MB，正常推送即可，不需要 Git LFS。

### 2. 在 Render 创建服务

1. 登录 <https://render.com>（GitHub 账号直接登录）
2. 点 **New +** → **Web Service**
3. 选择你刚推的仓库
4. 关键配置：

| 项 | 填什么 |
|---|---|
| Name | `ppt-master-service` |
| Region | Singapore（离国内近） |
| Runtime | **Docker** |
| Instance Type | **Free** |
| Health Check Path | `/api/health` |

5. 展开 **Environment Variables**，加两个：

| Key | Value |
|---|---|
| `DEEPSEEK_API_KEY` | 你的 `sk-...` |
| `SERVICE_TOKEN` | 随便编一个长字符串，比如 `ppt-2026-abc123xyz` |

6. 点 **Create Web Service**

首次构建约 3–5 分钟（要装 Python 依赖 + 中文字体）。

### 3. 拿到服务地址

部署成功后，Render 会给你一个地址：

```
https://ppt-master-service-xxxx.onrender.com
```

**先测一下**：

```bash
curl -X POST https://你的地址.onrender.com/api/build \
  -H "Content-Type: application/json" \
  -H "x-service-token: 你设的令牌" \
  -d '{"topic":"时间管理","audience":"大学生","pages":5,"style":"old-money"}' \
  --output test.pptx
```

等 20–40 秒，如果拿到 `test.pptx` 且能打开，说明服务正常。

---

## 三、配置 Cloudflare Worker

回到你的网站项目，设置两个环境变量：

**方式 A：命令行**

```bash
npx wrangler secret put PPT_SERVICE_URL
# 粘贴 https://你的地址.onrender.com

npx wrangler secret put PPT_SERVICE_TOKEN
# 粘贴你在 Render 设的令牌
```

**方式 B：网页控制台**

1. Cloudflare Dashboard → Workers & Pages → 你的 Worker
2. **Settings** → **Variables and Secrets**
3. 添加两个 Secret（不是普通变量）：`PPT_SERVICE_URL`、`PPT_SERVICE_TOKEN`
4. **重新 Deploy 一次**才会生效

然后重新构建部署：

```bash
node build.js
npx wrangler deploy
```

---

## 四、验证

打开网站 → 智能 PPT → 说一句想法 → 等 AI 出方案 → 点「导出 PPT」。

- **成功**：状态栏显示「PPT 已生成 · 8 页 · 23.4 秒」
- **服务没配**：自动回退到本地导出，状态栏显示「（本地）」
- **服务冷启动**：第一次请求可能要等 30–60 秒，属正常，重试一次即快

---

## 五、成本与限制

| 项 | 说明 |
|---|---|
| Render 免费层 | 512 MB 内存，够用；15 分钟无请求会休眠 |
| 冷启动 | 休眠后首次请求约 30–60 秒 |
| 单次生成耗时 | 5–40 秒（取决于页数和是否触发修复） |
| DeepSeek 调用 | 每页 1 次 + 修复轮次，8 页约 8–12 次调用 |
| 单次费用 | 约 1–3 分钱人民币 |

---

## 六、出问题时

| 现象 | 原因 / 处理 |
|---|---|
| `未配置 PPT_SERVICE_URL` | Worker 环境变量没设，或设完没重新部署 |
| `连接生成服务失败` | Render 服务挂了，去 Dashboard 看日志 |
| `生成超时` | 免费层冷启动，重试一次 |
| 返回的 PPT 有页是简版 | 那页模型连续 2 次没通过质检，用了降级版（`X-Deck-Fallback` 头会标出页码） |
| 中文显示成方块 | Dockerfile 里的 `fonts-noto-cjk` 没装上，检查构建日志 |

---

## 七、文件说明

| 文件 | 作用 |
|---|---|
| `app.py` | FastAPI 入口：`/api/build`、`/api/health` |
| `pipeline.py` | 核心流水线：大纲 → 逐页 SVG → 质检 → 修复 → 转换 |
| `prompt.py` | 提示词：SVG 契约、版式说明、修复指令 |
| `skill/` | ppt-master 脚本（裁剪版，仅含转换必需部分） |
| `Dockerfile` | 容器定义（含中文字体） |
| `render.yaml` | Render 蓝图（可选，用 Blueprint 部署时生效） |
