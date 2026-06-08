#!/usr/bin/env python3
"""
A股智能选股助手 — CLI 入口

用法:
    python run.py                  # 默认：对最近交易日运行分析
    python run.py --date 2026-06-08  # 指定日期
    python run.py --no-short       # 仅中线
    python run.py --no-mid         # 仅短线
    python run.py --clear-cache    # 清理缓存后运行
    python run.py --serve          # 启动 Web 面板
"""
import logging
import sys
from datetime import date
from pathlib import Path

import pandas as pd

# 将项目根目录加入 Python Path
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))


def setup_logging(verbose: bool = False):
    """配置日志"""
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    datefmt = "%H:%M:%S"
    logging.basicConfig(level=level, format=fmt, datefmt=datefmt)


def load_config():
    """加载配置文件"""
    import yaml

    config_path = PROJECT_ROOT / "config.yaml"
    if not config_path.exists():
        print("❌ 配置文件 config.yaml 不存在！")
        sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return config


def run_analysis(config: dict, target_date: date | None = None,
                 run_short: bool = True, run_mid: bool = True):
    """执行选股分析"""
    from src.data.calendar import is_trading_day, get_last_trading_day
    from src.data.fetcher import DataFetcher
    from src.engine.short_term import ShortTermStrategy
    from src.engine.mid_term import MidTermStrategy
    from src.output.reporter import Reporter
    from src.output.alert import AlertSender

    logger = logging.getLogger("main")

    # 确定目标日期
    if target_date is None:
        target_date = get_last_trading_day()

    # 交易日检查
    if not is_trading_day(target_date):
        logger.warning(f"⚠️ {target_date} 不是交易日，将使用最近交易日")
        target_date = get_last_trading_day(target_date)
        logger.info(f"使用交易日: {target_date}")

    # 初始化组件
    fetcher = DataFetcher(config.get("data", {}))

    # 市场概况
    market_spot = fetcher.fetch_market_spot()
    market_summary = {}
    if market_spot is not None and not market_spot.empty:
        up_count = (market_spot["pct_change"] > 0).sum() if "pct_change" in market_spot.columns else 0
        down_count = (market_spot["pct_change"] < 0).sum() if "pct_change" in market_spot.columns else 0
        avg_change = market_spot["pct_change"].mean() if "pct_change" in market_spot.columns else 0
        market_summary = {
            "上涨家数": f"{up_count}",
            "下跌家数": f"{down_count}",
            "平均涨跌幅": f"{avg_change:+.2f}%",
            "全市场成交额": "N/A",
        }

    # 短线分析
    short_result = None
    if run_short:
        logger.info("\n" + "🔥" * 20)
        logger.info("开始短线选股分析...")
        strategy_st = ShortTermStrategy(config, fetcher)
        short_result = strategy_st.run(target_date)

    # 中线分析
    mid_result = None
    if run_mid:
        logger.info("\n" + "📈" * 20)
        logger.info("开始中线选股分析...")
        strategy_mt = MidTermStrategy(config, fetcher)
        mid_result = strategy_mt.run(target_date)

    # 生成完整报告
    reporter = Reporter(config)
    short_df = short_result if short_result is not None and not short_result.empty else pd.DataFrame()
    mid_df = mid_result if mid_result is not None and not mid_result.empty else pd.DataFrame()
    reporter.generate(short_df, mid_df, target_date, market_summary)

    # 保存 JSON 数据（GitHub Pages 看板用）
    _save_report_json(short_df, mid_df, market_spot, target_date)

    # 推送微信通知
    alerter = AlertSender(config)
    from src.output.alert import build_push_summary
    push_msg = build_push_summary(short_df, mid_df, target_date, market_summary)
    alerter.send_report_summary(push_msg)

    # 终端摘要
    print_summary(short_df, mid_df)

    return short_df, mid_df


def _save_report_json(short_df, mid_df, market_spot, target_date):
    """保存 report.json 供 GitHub Pages 看板使用"""
    import json
    from pathlib import Path

    report = {"date": target_date.isoformat()}
    report["short_term"] = _df_safe(short_df)
    report["mid_term"] = _df_safe(mid_df)

    # 市场概况（数值型，不用中文 key）
    if market_spot is not None and not market_spot.empty:
        up = int((market_spot["pct_change"] > 0).sum()) if "pct_change" in market_spot.columns else 0
        down = int((market_spot["pct_change"] < 0).sum()) if "pct_change" in market_spot.columns else 0
        avg = float(market_spot["pct_change"].mean()) if "pct_change" in market_spot.columns else 0
        report["market"] = {
            "up_count": up,
            "down_count": down,
            "avg_change": f"{avg:+.2f}%",
        }
    else:
        report["market"] = {}

    # 写到 docs/ (GitHub Pages 根目录)
    out = Path(PROJECT_ROOT) / "docs" / "report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, default=str), encoding="utf-8")


def _df_safe(df) -> list:
    """DataFrame 安全转字典列表（处理 numpy 类型）"""
    import numpy as np

    if df is None or df.empty:
        return []
    result = []
    for idx, row in df.iterrows():
        d = {}
        for col in df.columns:
            val = row[col]
            if isinstance(val, (np.integer,)):
                d[col] = int(val)
            elif isinstance(val, (np.floating,)):
                d[col] = float(val) if not np.isnan(val) else None
            elif isinstance(val, np.bool_):
                d[col] = bool(val)
            else:
                d[col] = str(val) if pd.isna(val) else val
        # 确保 code 字段
        if "code" not in d:
            d["code"] = str(idx)
        result.append(d)
    return result


def print_summary(short_df, mid_df):
    """打印命令行摘要"""
    logger = logging.getLogger("main")

    logger.info("\n" + "=" * 50)
    logger.info("📊 分析完成！")

    if hasattr(short_df, 'empty') and not short_df.empty:
        logger.info(f"\n⚡ 短线推荐 ({len(short_df)} 支):")
        for i, (idx, row) in enumerate(short_df.iterrows(), 1):
            name = row.get("name", "")
            code = row.get("code", idx)
            score = row.get("total_score", 0)
            logger.info(f"  {i}. {name} ({code}) — 评分: {score:.1f}")

    if hasattr(mid_df, 'empty') and not mid_df.empty:
        logger.info(f"\n🏔️ 中线推荐 ({len(mid_df)} 支):")
        for i, (idx, row) in enumerate(mid_df.iterrows(), 1):
            name = row.get("name", "")
            code = row.get("code", idx)
            score = row.get("total_score", 0)
            logger.info(f"  {i}. {name} ({code}) — 评分: {score:.1f}")

    if ((hasattr(short_df, 'empty') and short_df.empty) and
            (hasattr(mid_df, 'empty') and mid_df.empty)):
        logger.info("\n⚠️ 今日无推荐，市场可能处于调整期")

    logger.info("=" * 50)


def serve_web(config: dict):
    """启动 Web 面板"""
    try:
        from web.app import create_app
        app = create_app(config)
        import uvicorn
        uvicorn.run(app, host="127.0.0.1", port=8080, log_level="info")
    except ImportError as e:
        print(f"❌ Web 面板依赖未安装: {e}")
        print("请运行: pip install fastapi uvicorn jinja2")
        sys.exit(1)


def main():
    """CLI 主入口"""
    import argparse

    parser = argparse.ArgumentParser(
        description="A股智能选股助手 — 短线+中线量化选股"
    )
    parser.add_argument(
        "--date", "-d",
        type=str,
        default=None,
        help="目标日期 (YYYY-MM-DD)，默认最近交易日",
    )
    parser.add_argument(
        "--no-short",
        action="store_true",
        help="跳过短线选股",
    )
    parser.add_argument(
        "--no-mid",
        action="store_true",
        help="跳过中线选股",
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="运行前清理数据缓存",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="详细日志输出",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="启动 Web 看板面板",
    )

    args = parser.parse_args()

    # 日志
    setup_logging(args.verbose)

    # 配置
    config = load_config()

    # 清理缓存
    if args.clear_cache:
        from src.data.cache import clear_cache
        clear_cache()

    # Web 模式
    if args.serve:
        serve_web(config)
        return

    # 解析日期
    target_date = None
    if args.date:
        try:
            target_date = date.fromisoformat(args.date)
        except ValueError:
            print(f"❌ 日期格式错误: {args.date}，请使用 YYYY-MM-DD")
            sys.exit(1)

    # 运行分析
    run_analysis(
        config,
        target_date=target_date,
        run_short=not args.no_short,
        run_mid=not args.no_mid,
    )


if __name__ == "__main__":
    main()
