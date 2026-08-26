# 预制梁台车追踪系统

工厂 RFID 台车定位后台。依据 PRD v0.5 与 Omada 原型实现：**登录、项目看板、三步向导、项目信息（基本信息 / 映射表 / 报文模板）、高吞吐设备上报与解析引擎**。

本版不做页面、但已预留：**项目内主看板、原始数据监控、系统日志**。技术方案见 [docs/PROTOCOL.md](docs/PROTOCOL.md)。

## 快速启动

默认用 **SQLite**（`backend/trolley.db`）即可本地跑通；Redis 未启动时上报改为同步解析。生产建议 PostgreSQL + Redis（见 `docker-compose.yml`）。

```bash
# 1. 后端
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# 3. 前端（另开终端）
cd frontend
npm install
npm run dev
```

浏览器打开 http://localhost:5173  
默认账号：`admin` / `omada2026`

一键 Docker（含前后端）：

```bash
docker compose up --build
```

前端映射到 http://localhost:5173 ，API 为 http://localhost:8000 。

## 新建项目与映射表

1. 登录 → 新建项目，填 PID / 名称 / 监控文件夹。
2. Step2 导入两张映射表一：`templates/位置-编号映射表.xlsx` + `templates/编号-映射表.xlsx`。
3. 再导入映射表二：`templates/港台座编号映射.xlsx`。
4. Step3 可跳过（默认报文模板）。
5. 进入「项目信息」可单项编辑产线 / 工序、拖拽工序顺序。

页面也可下载空白模板。现场文件第 6 列表头即使写成「区域」，也会按区域 ID 解析。

## 设备上报

```http
POST /api/v1/ingest/report
X-Ingest-Token: <项目创建或重置时返回的 token>
Content-Type: application/json

{
  "deviceId": "LYG1",
  "hex": "E2806894000040358004310A",
  "epcs": ["E2 80 68 94 00 00 40 35 80 04 31 0A"],
  "sportState": "moving"
}
```

成功返回 **202**，只表示入队。解析结果写入 `latest_locations`（后续主看板读取）。Token 在项目信息页「重置上报 Token」后重新发放，且只显示一次。

模拟上报：

```bash
python scripts/simulate_report.py --token <token> --device LYG1 --epc "E2806894000040358004310A"
```

## 目录

```
backend/     FastAPI + 解析引擎 + Redis Stream 接入
frontend/    Omada 风格管理端
templates/   三份现场 Excel 模板
docs/        实现方案
scripts/     上报模拟
```
