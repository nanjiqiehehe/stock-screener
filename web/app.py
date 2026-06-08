"""
Web 看板 — FastAPI 应用 (无模板文件依赖)
"""
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>A股智能选股助手</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: 'Microsoft YaHei', sans-serif; background: #0d1117; color: #c9d1d9; min-height: 100vh; }
.header { background: #161b22; border-bottom: 1px solid #30363d; padding: 16px 40px; display: flex; justify-content: space-between; align-items: center; }
.header h1 { font-size: 20px; color: #58a6ff; }
.container { max-width: 1400px; margin: 0 auto; padding: 20px 40px; }
.controls { display: flex; gap: 10px; margin-bottom: 20px; align-items: center; }
.btn { padding: 10px 20px; border: 1px solid #30363d; border-radius: 6px; background: #21262d; color: #c9d1d9; cursor: pointer; font-size: 14px; }
.btn:hover { background: #30363d; border-color: #58a6ff; }
.btn.primary { background: #238636; border-color: #2ea043; color: #fff; }
.btn.primary:hover { background: #2ea043; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.section-title { font-size: 18px; font-weight: bold; margin: 20px 0 12px; padding-bottom: 8px; border-bottom: 2px solid #30363d; }
.section-title.short { color: #f0883e; border-color: #f0883e; }
.section-title.mid { color: #58a6ff; border-color: #58a6ff; }
table { width: 100%; border-collapse: collapse; margin-bottom: 20px; background: #161b22; border-radius: 8px; }
th, td { padding: 8px 12px; text-align: left; border-bottom: 1px solid #21262d; font-size: 13px; }
th { background: #21262d; color: #8b949e; font-weight: 600; }
tr:hover td { background: #1c2128; }
.score-hi { color: #3fb950; font-weight: bold; }
.score-mid { color: #d29922; }
.pct-up { color: #f85149; }
.empty { text-align: center; padding: 40px; color: #484f58; background: #161b22; border-radius: 8px; }
.spin { display: inline-block; width: 18px; height: 18px; border: 3px solid #30363d; border-top-color: #58a6ff; border-radius: 50%; animation: s 0.8s linear infinite; }
@keyframes s { to { transform: rotate(360deg); } }
</style>
</head>
<body>
<div class="header"><h1>A股智能选股助手</h1><span id="date"></span></div>
<div class="container">
<div class="controls">
<button class="btn primary" onclick="run()" id="btn">运行分析</button>
<button class="btn" onclick="loadHistory()">历史报告</button>
<span id="status" style="color: #8b949e; font-size: 13px;"></span>
</div>
<div id="short"><div class="section-title short">短线精选</div><div id="sc"></div></div>
<div id="mid"><div class="section-title mid">中线趋势股</div><div id="mc"></div></div>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;">
<div style="background:#161b22;border-radius:8px;padding:16px;height:380px;" id="chart1"></div>
<div style="background:#161b22;border-radius:8px;padding:16px;height:380px;" id="chart2"></div>
</div>
</div>
<script>
document.getElementById('date').textContent = new Date().toLocaleDateString('zh-CN');
async function run() {
    const btn = document.getElementById('btn'), st = document.getElementById('status');
    btn.disabled = true; btn.innerHTML = '<span class="spin"></span> 分析中...';
    st.textContent = '正在获取数据...';
    try {
        const resp = await fetch('/api/run'), data = await resp.json();
        renderShort(data.short_term || []); renderMid(data.mid_term || []); renderCharts(data);
        st.textContent = '完成 ' + new Date().toLocaleTimeString();
    } catch(e) { st.textContent = '失败: ' + e.message; }
    finally { btn.disabled = false; btn.innerHTML = '运行分析'; }
}
function renderShort(arr) {
    const c = document.getElementById('sc');
    if (!arr.length) { c.innerHTML = '<div class="empty">今日无短线推荐</div>'; return; }
    let h = '<table><tr><th>#</th><th>代码</th><th>名称</th><th>总分</th><th>涨跌</th><th>资金</th><th>情绪</th><th>技术</th><th>流通</th><th>理由</th></tr>';
    arr.forEach((s,i) => {
        const t = s.total_score||0, cl = t>=40?'score-hi':t>=30?'score-mid':'';
        const p = s.pct_change||0;
        h += `<tr><td>${i+1}</td><td>${s.code||'-'}</td><td><b>${s.name||'-'}</b></td><td class="${cl}">${t.toFixed(1)}</td><td class="pct-up">${p>0?'+':''}${p.toFixed(2)}%</td><td>${(s.score_capital||0).toFixed(0)}</td><td>${(s.score_sentiment||0).toFixed(0)}</td><td>${(s.score_technical||0).toFixed(0)}</td><td>${(s.score_liquidity||0).toFixed(0)}</td><td><small>${getReason(s)}</small></td></tr>`;
    });
    h += '</table>'; c.innerHTML = h;
}
function renderMid(arr) {
    const c = document.getElementById('mc');
    if (!arr.length) { c.innerHTML = '<div class="empty">今日无中线推荐（无股票达标）</div>'; return; }
    let h = '<table><tr><th>#</th><th>代码</th><th>名称</th><th>总分</th><th>趋势</th><th>基本面</th><th>资金沉淀</th><th>行业</th><th>理由</th></tr>';
    arr.forEach((s,i) => {
        const t = s.total_score||0;
        h += `<tr><td>${i+1}</td><td>${s.code||'-'}</td><td><b>${s.name||'-'}</b></td><td>${t.toFixed(1)}</td><td>${(s.score_trend||0).toFixed(0)}</td><td>${(s.score_fundamentals||0).toFixed(0)}</td><td>${(s.score_capital_acc||0).toFixed(0)}</td><td>${(s.score_industry||0).toFixed(0)}</td><td><small>${getMidReason(s)}</small></td></tr>`;
    });
    h += '</table>'; c.innerHTML = h;
}
function renderCharts(data) {
    const st = data.short_term || [];
    const c1 = echarts.init(document.getElementById('chart1'));
    c1.setOption({title:{text:'短线评分构成',textStyle:{color:'#c9d1d9',fontSize:14}},tooltip:{trigger:'axis'},xAxis:{type:'category',data:st.slice(0,8).map(s=>s.name||s.code),axisLabel:{color:'#8b949e',fontSize:10}},yAxis:{type:'value',max:100,axisLabel:{color:'#8b949e'}},series:[{name:'资金',type:'bar',data:st.slice(0,8).map(s=>s.score_capital||0),itemStyle:{color:'#f0883e'}},{name:'情绪',type:'bar',data:st.slice(0,8).map(s=>s.score_sentiment||0),itemStyle:{color:'#d29922'}},{name:'技术',type:'bar',data:st.slice(0,8).map(s=>s.score_technical||0),itemStyle:{color:'#58a6ff'}},{name:'流通',type:'bar',data:st.slice(0,8).map(s=>s.score_liquidity||0),itemStyle:{color:'#3fb950'}}],legend:{textStyle:{color:'#8b949e',fontSize:10}}});
    const sec = {}; st.forEach(s => { const ind = s.industry || '其他'; sec[ind] = (sec[ind]||0)+1; });
    const pd = Object.entries(sec).map(([n,v]) => ({name:n,value:v}));
    const c2 = echarts.init(document.getElementById('chart2'));
    c2.setOption({title:{text:'行业分布',textStyle:{color:'#c9d1d9',fontSize:14}},tooltip:{trigger:'item'},series:[{type:'pie',radius:['30%','70%'],data:pd,label:{color:'#8b949e',fontSize:10}}]});
}
async function loadHistory() {
    const resp = await fetch('/api/history'), data = await resp.json();
    document.getElementById('status').textContent = data.reports.length ? '最近报告: ' + data.reports[0].date : '暂无历史';
}
function getReason(s) {
    const p = []; if (s.consecutive>=2) p.push(s.consecutive+'连板');
    if (s.pct_change>=9) p.push('涨停'); if (s.score_capital>=65) p.push('主力流入');
    if (s.dragon_tiger_net>0) p.push('龙虎榜'); return p.join('+') || '多因子共振';
}
function getMidReason(s) {
    const p = []; if (s.score_trend>=65) p.push('趋势多头');
    if (s.score_fundamentals>=60) p.push('基本面优');
    if (s.north_flow_days>=10) p.push('北向流入'); return p.join('+') || '中长期配置';
}
</script>
</body>
</html>"""


def _load_config():
    import yaml
    config_path = PROJECT_ROOT / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _run_analysis(config, target_date=None, run_short=True, run_mid=True):
    import pandas as pd
    from src.data.calendar import is_trading_day, get_last_trading_day
    from src.data.fetcher import DataFetcher
    from src.engine.short_term import ShortTermStrategy
    from src.engine.mid_term import MidTermStrategy

    if target_date is None:
        target_date = get_last_trading_day()
    if not is_trading_day(target_date):
        target_date = get_last_trading_day(target_date)

    fetcher = DataFetcher(config.get("data", {}))
    short_result = ShortTermStrategy(config, fetcher).run(target_date) if run_short else None
    mid_result = MidTermStrategy(config, fetcher).run(target_date) if run_mid else None

    return (
        short_result if short_result is not None and not short_result.empty else pd.DataFrame(),
        mid_result if mid_result is not None and not mid_result.empty else pd.DataFrame(),
    )


def create_app(config=None):
    app = FastAPI(title="A股智能选股", version="1.0")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @app.get("/", response_class=HTMLResponse)
    async def home():
        return HTMLResponse(content=HTML_PAGE)

    @app.get("/api/run")
    async def api_run(date: str | None = Query(None), run_short: bool = Query(True), run_mid: bool = Query(True)):
        cfg = config or _load_config()
        target_date = date.fromisoformat(date) if date else None
        short_df, mid_df = _run_analysis(cfg, target_date, run_short, run_mid)
        return {
            "short_term": _df_to_list(short_df),
            "mid_term": _df_to_list(mid_df),
        }

    @app.get("/api/history")
    async def api_history():
        reports_dir = PROJECT_ROOT / "reports"
        files = sorted(reports_dir.glob("*.md"), reverse=True) if reports_dir.exists() else []
        return {"reports": [{"date": f.stem} for f in files[:30]]}

    return app


def _df_to_list(df) -> list:
    import pandas as pd
    if df is None or (isinstance(df, pd.DataFrame) and df.empty):
        return []
    return df.reset_index().to_dict(orient="records")


if __name__ == "__main__":
    import uvicorn
    app = create_app(_load_config())
    uvicorn.run(app, host="127.0.0.1", port=8080, log_level="info")
