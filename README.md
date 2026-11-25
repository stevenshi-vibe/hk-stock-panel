# 港股 CCASS 监控面板 📊

> 本地部署的港股 CCASS 席位监控工具。交易者看异动资金流向，IR 看老股东减持抛压。一键启动，零费用，数据不出本机。

中文 | [English](#english)

---

## 功能一览

- 🏦 **CCASS 席位持仓**：日频追踪 Top10 席位变动；查看范围可选 30/60/90/180/365 天（默认 60）。库里不够时按现有限速分批补抓，最长不超过港交所约 12 个月
- 🌊 **资金流向**：每日净流向汇总图、Top10 席位净买卖堆叠图、可选历史截止日期（1D/5D/10D/20D/60D，并支持 90/180/365）
- 🔗 **南向资金**：港股通每日净买卖监控
- 🧭 **南下资金拆解**：把南下合计持股拆成「确认经港股通的公募」「可能经港股通」「非港股通/权益披露」和「未识别」，并给出覆盖率。见 [docs/southbound.md](docs/southbound.md)
- 📈 **行情 + 公告**：K线图 + 港交所公告自动抓取与事件标注
- 🚨 **异动信号**：大额席位变动/新入退出机构/放量异常，0-100 评分
- 💰 **成本分析**（IR模式）：上传股东名册后，自动归因席位 → 成本区间分布 → 减持压力情景分析
- 📅 **解禁日历**：解禁后席位流向自动追踪
- 📝 **自动报告**：日报/周报/月报一键生成（HTML + Markdown + Excel）
- ⏰ **每日自动更新**：设置页可选启用，电脑不关机即自动抓取
- 🌐 **中英双语**：券商/股票名自动翻译

## 快速开始

```bash
git clone https://github.com/stevenshi-vibe/hk-stock-panel.git
cd hk-stock-panel
chmod +x start.sh
./start.sh
```

浏览器打开 http://localhost:3003

首次使用：
1. 输入港股代码（如 00700），自动抓取 CCASS + 行情 + 公告
2. 右上角 ⚙️ 设置页可配置：自动更新时间、LLM API Key（可选，用于 AI 报告）、Webhook 推送（可选）
3. IR 模式：在"🔒 IR设置"页上传股东名册（Excel/CSV/PDF）

## 系统要求

- Python 3.10+
- Node.js 18+（仅首次build时需要，后续运行不需要）
- Windows / macOS / Linux
- 约 200MB 磁盘空间

> ⚠️ **安全提示**：本工具为本地单用户使用，默认绑定 `0.0.0.0:3003`（方便局域网访问）。请勿直接暴露到公网，无内置身份认证。详见 [SECURITY.md](SECURITY.md)。

## 数据源

全部免费公开，无需 API Key：HKEX 披露易 / CCASS Search / 腾讯财经 / Yahoo Finance

南下拆解额外用到东方财富/天天基金的网页接口，以及港交所权益披露日报和港股通持股页。这些通道**没有商用授权**。港交所 CCASS / 港股通页面条款限于个人、非商业使用，禁止把程序化抓取结果做成再分发的数据库。代码里的 provider 可以换成持牌 CSV：设置环境变量 `SOUTHBOUND_LICENSED_CSV`。合规说明和口径见 [docs/southbound.md](docs/southbound.md)。

## 技术栈

- **后端**：FastAPI + SQLite（Python 3.10+）
- **前端**：React 18 + Vite + Tailwind + Recharts
- **平台**：Windows / Mac / Linux 均可运行

---

<a id="english"></a>
## English

A locally-deployed HK stock CCASS monitoring dashboard. Track participant flows, Southbound Connect holdings, anomaly signals, and shareholder selloff pressure — all in one place. No paid data, no cloud, no login.

```bash
git clone https://github.com/stevenshi-vibe/hk-stock-panel.git
cd hk-stock-panel
chmod +x start.sh
./start.sh
```
Open http://localhost:3003

**Requirements**: Python 3.10+, Node.js 18+ (first build only) | **Data sources**: HKEX, Tencent Finance, Yahoo Finance (all free) | **Stack**: FastAPI + SQLite + React + Recharts

The southbound breakdown also reads Eastmoney/TianTian fund pages and HKEX disclosure pages. Those sources are not licensed for commercial redistribution. HKEX's CCASS / Stock Connect pages are personal, non-commercial use. Swap in a licensed CSV with `SOUTHBOUND_LICENSED_CSV`. Details: [docs/southbound.md](docs/southbound.md).

> ⚠️ **Security note**: This is a local single-user tool. It binds to `0.0.0.0:3003` by default for LAN access. Do NOT expose directly to the public internet — there is no built-in authentication. See [SECURITY.md](SECURITY.md) for details.

---

*⚠️ 仅供参考，不构成投资建议。CCASS 数据为 T+2。*
