"""
中线选股策略引擎

筛选管线：
全市场 → 市值过滤 → PE分位过滤 → 均线趋势过滤 →
北向资金过滤 → 多因子打分 → 行业分散 → 输出 Top 1-2
（分数不达标则不推荐）
"""
import logging
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from ..data.fetcher import DataFetcher
from ..data.calendar import get_last_trading_day
from .scorer import MidTermScorer

logger = logging.getLogger(__name__)


class MidTermStrategy:
    """中线选股策略"""

    def __init__(self, config: dict, fetcher: Optional[DataFetcher] = None):
        self.config = config
        self.cfg = config.get("mid_term", {})
        self.filter_cfg = self.cfg.get("filter", {})
        self.scorer = MidTermScorer(config)
        self.fetcher = fetcher or DataFetcher(config.get("data", {}))

    def run(self, target_date: Optional[date] = None) -> pd.DataFrame:
        """
        执行中线选股流程。

        Args:
            target_date: 目标交易日

        Returns:
            推荐股票 DataFrame，按 total_score 降序，仅保留达标的
        """
        if target_date is None:
            target_date = get_last_trading_day()

        logger.info(f"========== 中线选股开始 (日期: {target_date}) ==========")

        # Step 1: 全市场
        market = self.fetcher.fetch_market_spot()
        if market is None or market.empty:
            logger.error("全市场行情为空，终止中线选股")
            return pd.DataFrame()

        # Step 2: 过滤
        candidates = self._filter(market, target_date)
        logger.info(f"中线过滤后候选池: {len(candidates)} 支")

        if candidates.empty:
            logger.warning("无股票通过中线过滤条件")
            return pd.DataFrame()

        # Step 3: 增强数据
        enriched = self._enrich(candidates, target_date)
        logger.info(f"中线数据增强完成: {len(enriched)} 支")

        # Step 4: 打分
        scored = self.scorer.score(enriched)

        # Step 5: 仅保留达标的
        min_score = self.cfg.get("min_score_threshold", 65)
        qualified = scored[scored["total_score"] >= min_score]
        logger.info(f"中线达标股票 (>= {min_score}分): {len(qualified)} 支")

        if qualified.empty:
            logger.info("今日无中线推荐（无股票达标）")
            return pd.DataFrame()

        # Step 6: 行业分散
        diversified = self._diversify_by_industry(qualified)
        max_count = self.cfg.get("output_count", 2)
        result = diversified.head(max_count)

        logger.info(f"========== 中线选股完成: 推荐 {len(result)} 支 ==========")
        return result

    def _filter(self, market: pd.DataFrame, target_date: date) -> pd.DataFrame:
        """
        中线初筛过滤。

        条件：
        1. 流通市值 50亿~500亿
        2. PE > 0
        3. 排除 ST
        """
        df = market.copy()
        df["code"] = df["code"].astype(str).str.strip().str.zfill(6)

        # 排除 ST
        if "name" in df.columns:
            df = df[~df["name"].astype(str).str.contains(r"ST|退|\*ST", na=False)]

        # 流通市值过滤
        min_cap = self.filter_cfg.get("min_float_market_cap", 50)
        max_cap = self.filter_cfg.get("max_float_market_cap", 500)
        if "float_market_cap" in df.columns:
            cap = df["float_market_cap"].copy()
            if cap.median() > 10000:
                cap = cap / 1e8
            df = df[(cap >= min_cap) & (cap <= max_cap)]

        # PE > 0（不盈利的公司排除）
        if "pe" in df.columns:
            df = df[(df["pe"] > 0) & (df["pe"] < 200)]

        df = df.set_index("code")
        return df

    def _enrich(self, candidates: pd.DataFrame, target_date: date) -> pd.DataFrame:
        """
        中线数据增强：
        - 技术指标（周线级别趋势）
        - 北向资金连续流入情况
        - 板块相对强度
        """
        df = candidates.copy()

        # ---- 技术指标（取前30支获取K线） ----
        codes = df.index.tolist()[:30]
        indicators = self._batch_fetch_mid_indicators(codes)
        if not indicators.empty:
            for col in indicators.columns:
                df[col] = indicators[col]

        # ---- 北向资金连续流入 ----
        north = self.fetcher.fetch_north_bound(days=20)
        if not north.empty and "net_inflow" in north.columns:
            # 近20日北向整体净流入天数
            north_positive_days = (north["net_inflow"] > 0).sum()
            # 简化：所有候选股给予相同的北向背景分
            df["north_flow_days"] = north_positive_days
            df["north_flow_positive"] = north_positive_days >= 10

        # ---- 板块相对强度 ----
        sector_flow = self.fetcher.fetch_sector_capital_flow()
        if not sector_flow.empty and "pct_change" in sector_flow.columns:
            if "industry" in df.columns:
                sector_pct = dict(zip(
                    sector_flow["sector_name"],
                    sector_flow["pct_change"] if "pct_change" in sector_flow.columns
                    else [0] * len(sector_flow)
                ))
                df["sector_relative_strength"] = df["industry"].map(sector_pct)

        # ---- ROE和营收增速（来自基本面数据，如不可取则用默认值） ----
        if "roe" not in df.columns:
            df["roe"] = 10  # 默认值
        if "revenue_growth" not in df.columns:
            df["revenue_growth"] = 15  # 默认值

        # 填充缺失
        for col in df.columns:
            if df[col].dtype in [np.float64, np.int64, float, int]:
                df[col] = df[col].fillna(0)

        return df

    def _batch_fetch_mid_indicators(self, codes: list[str]) -> pd.DataFrame:
        """获取中线所需技术指标（日线60天 + 周线）"""
        rows = []
        for code in codes:
            try:
                # 日线
                daily = self.fetcher.fetch_history_kline(code, period=60, freq="daily")
                if daily is None or daily.empty or len(daily) < 20:
                    continue

                latest = daily.iloc[-1]
                row = {"code": code}

                # 均线
                for ma in [5, 10, 20, 60]:
                    col = f"ma{ma}"
                    if col in daily.columns:
                        row[col] = latest[col]

                # 60日均线斜率（过去5日均线的变化率）
                if "ma60" in daily.columns and len(daily) >= 10:
                    ma60_recent = daily["ma60"].dropna()
                    if len(ma60_recent) >= 5:
                        row["ma60_slope"] = (
                            (ma60_recent.iloc[-1] - ma60_recent.iloc[-5]) /
                            ma60_recent.iloc[-5] * 100
                        )
                    else:
                        row["ma60_slope"] = 0
                else:
                    row["ma60_slope"] = 0

                # MACD
                for macd_col in ["macd_dif", "macd_dea", "macd_bar"]:
                    if macd_col in daily.columns:
                        row[macd_col] = latest[macd_col]

                #布林带
                for bb in ["boll_mid", "boll_upper", "boll_lower"]:
                    if bb in daily.columns:
                        row[bb] = latest[bb]

                # RSI
                if "rsi" in daily.columns:
                    row["rsi"] = latest["rsi"]

                # 换手率和流通市值
                if "turnover_rate" in daily.columns:
                    row["turnover_rate"] = latest["turnover_rate"]
                if "float_market_cap" in daily.columns:
                    row["float_market_cap"] = latest["float_market_cap"]

                rows.append(row)

            except Exception as e:
                logger.debug(f"中线K线获取失败 {code}: {e}")
                continue

        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows).set_index("code")

    def _diversify_by_industry(self, scored: pd.DataFrame) -> pd.DataFrame:
        """
        行业分散：每个行业只选取最优的1支。
        """
        if "industry" not in scored.columns:
            return scored

        industry_best = {}
        non_industry_indices = []

        for idx, row in scored.iterrows():
            sector = str(row.get("industry", "")).strip()
            if not sector or sector in ("nan", "0", ""):
                non_industry_indices.append(idx)
                continue

            if sector not in industry_best:
                industry_best[sector] = idx

        # 合并：各行业最优 + 无行业信息的
        result_indices = list(industry_best.values()) + non_industry_indices
        return scored.loc[scored.index.isin(result_indices)]
