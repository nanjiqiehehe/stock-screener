"""
短线选股策略引擎

筛选管线：
全市场 → 过滤ST/新股/大盘股 → 涨幅过滤 → 换手率过滤 →
多因子打分 → 板块去重 → 输出 Top N
"""
import logging
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from ..data.fetcher import DataFetcher
from ..data.calendar import get_last_trading_day
from .scorer import ShortTermScorer

logger = logging.getLogger(__name__)


class ShortTermStrategy:
    """短线选股策略"""

    def __init__(self, config: dict, fetcher: Optional[DataFetcher] = None):
        self.config = config
        self.cfg = config.get("short_term", {})
        self.filter_cfg = self.cfg.get("filter", {})
        self.scorer = ShortTermScorer(config)
        self.fetcher = fetcher or DataFetcher(config.get("data", {}))

    def run(self, target_date: Optional[date] = None) -> pd.DataFrame:
        """
        执行短线选股流程。

        Args:
            target_date: 目标交易日，None=最近交易日

        Returns:
            推荐股票 DataFrame，按 total_score 降序排列
        """
        if target_date is None:
            target_date = get_last_trading_day()

        logger.info(f"========== 短线选股开始 (日期: {target_date}) ==========")

        from datetime import date as dt
        is_today = (target_date == dt.today())

        # Step 1: 获取候选股票池
        # 优先从涨停板+龙虎榜获取（日期精确），再补充市场行情
        candidates = self._build_candidate_pool(target_date, is_today)
        logger.info(f"候选池: {len(candidates)} 支")

        if candidates.empty:
            logger.warning("无短线候选股票")
            return pd.DataFrame()

        # Step 2: 增强数据（龙虎榜、板块热度、K线技术指标）
        enriched = self._enrich(candidates, target_date)
        logger.info(f"数据增强完成: {len(enriched)} 支")

        # Step 3: 打分
        scored = self.scorer.score(enriched)
        logger.info(f"打分完成: Top5 得分 = {scored['total_score'].head(5).tolist()}")

        # Step 4: 板块去重
        deduped = self._dedup_by_sector(scored)
        max_count = self.cfg.get("output_count", 8)
        result = deduped.head(max_count)

        logger.info(f"========== 短线选股完成: 推荐 {len(result)} 支 ==========")
        return result

    def _build_candidate_pool(self, target_date: date, is_today: bool) -> pd.DataFrame:
        """
        构建候选股票池。
        优先从日期精确的涨停板+龙虎榜数据获取，
        如果是今天则补充全市场行情中的强势股。
        """
        import akshare as ak

        codes_set = set()
        rows = []

        # 1. 涨停板股票（日期精确）
        limit_up = self.fetcher.fetch_limit_up(target_date)
        if not limit_up.empty and "code" in limit_up.columns:
            for _, row in limit_up.iterrows():
                code = str(row["code"]).strip().zfill(6)
                if code not in codes_set:
                    codes_set.add(code)
                    rows.append({
                        "code": code,
                        "name": row.get("name", ""),
                        "pct_change": row.get("pct_change", 9.9),
                        "close": row.get("close", 0),
                        "turnover_rate": row.get("turnover_rate", 0),
                        "consecutive": row.get("consecutive", 0),
                        "seal_amount": row.get("seal_amount", 0),
                        "break_count": row.get("break_count", 0),
                        "industry": row.get("industry", ""),
                        "float_market_cap": row.get("float_market_cap", np.nan),
                    })

        # 2. 龙虎榜股票（日期精确）
        dt_data = self.fetcher.fetch_dragon_tiger(target_date)
        if not dt_data.empty and "code" in dt_data.columns:
            for _, row in dt_data.iterrows():
                code = str(row["code"]).strip().zfill(6)
                if code not in codes_set:
                    codes_set.add(code)
                    rows.append({
                        "code": code,
                        "name": row.get("name", ""),
                        "pct_change": row.get("pct_change", np.nan),
                        "close": row.get("close", np.nan),
                        "dragon_tiger_net": row.get("net_buy", 0),
                        "industry": "",
                    })

        # 3. 如果是今天，再补充全市场强势股（涨幅>3%的）
        if is_today:
            market = self.fetcher.fetch_market_spot()
            if market is not None and not market.empty:
                min_change = self.filter_cfg.get("min_daily_change", 3.0)
                if "pct_change" in market.columns and "code" in market.columns:
                    strong = market[market["pct_change"] >= min_change]
                    for _, row in strong.iterrows():
                        code = str(row["code"]).strip().zfill(6)
                        if code not in codes_set:
                            codes_set.add(code)
                            rows.append({
                                "code": code,
                                "name": row.get("name", ""),
                                "pct_change": row.get("pct_change", 0),
                                "close": row.get("close", 0),
                                "turnover_rate": row.get("turnover_rate", 0),
                                "float_market_cap": row.get("float_market_cap", np.nan),
                                "industry": "",
                            })

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        df = df.set_index("code")

        # 应用过滤（排除ST、市值上限等）
        df = self._filter(df, target_date)
        return df

    def _filter(self, market: pd.DataFrame, target_date: date) -> pd.DataFrame:
        """
        初筛过滤。

        过滤条件：
        1. 排除 ST/*ST/退市
        2. 流通市值不超过 max_float_market_cap 亿
        3. 当日涨幅 >= min_daily_change
        4. 换手率在合理范围
        """
        df = market.copy()

        # 1. 排除 ST
        if "name" in df.columns:
            df = df[~df["name"].astype(str).str.contains(r"ST|退|\*ST", na=False)]

        # 2. 流通市值过滤（如果列存在）
        max_cap = self.filter_cfg.get("max_float_market_cap", 500)
        if "float_market_cap" in df.columns and df["float_market_cap"].notna().any():
            cap = df["float_market_cap"].copy()
            if cap.median() > 10000:
                cap = cap / 1e8
            # 只过滤有值的，保留NaN的
            mask = cap.isna() | ((cap > 0) & (cap <= max_cap))
            df = df[mask]

        # 3. 涨幅过滤
        min_change = self.filter_cfg.get("min_daily_change", 3.0)
        if "pct_change" in df.columns:
            df = df[df["pct_change"].fillna(0) >= min_change]

        # 4. 换手率过滤
        min_turnover = self.filter_cfg.get("min_turnover_rate", 5.0)
        max_turnover = self.filter_cfg.get("max_turnover_rate", 25.0)
        if "turnover_rate" in df.columns and df["turnover_rate"].notna().any():
            tr = df["turnover_rate"].fillna(0)
            mask = tr.isna() | ((tr >= min_turnover) & (tr <= max_turnover))
            df = df[mask]

        return df

    def _enrich(self, candidates: pd.DataFrame, target_date: date) -> pd.DataFrame:
        """
        增强候选池数据：
        - 合并龙虎榜净买入
        - 合并板块热度排名
        - 计算技术指标（从K线）
        """
        df = candidates.copy()

        # ---- 龙虎榜数据 ----
        # 先删掉 candidate pool 中已有的同名列，再 join
        for pre_col in ["dragon_tiger_net", "dt_buy_count", "hot_money_ratio"]:
            if pre_col in df.columns:
                del df[pre_col]

        dt_data = self.fetcher.fetch_dragon_tiger(target_date)
        if not dt_data.empty and "code" in dt_data.columns and "net_buy" in dt_data.columns:
            dt_agg = dt_data.groupby("code").agg(
                dragon_tiger_net=("net_buy", "sum"),
                dt_buy_count=("net_buy", "count"),
            )
            if "dt_buy_count" in dt_agg.columns:
                dt_agg["hot_money_ratio"] = dt_agg["dt_buy_count"] / dt_agg["dt_buy_count"].max()
            df = df.join(dt_agg, how="left")

        # ---- 板块热度 ----
        sector_flow = self.fetcher.fetch_sector_capital_flow()
        if not sector_flow.empty and "sector_name" in sector_flow.columns:
            # 将板块排名映射到个股（通过涨停板数据中的 industry 字段）
            if "industry" in df.columns:
                sector_rank_map = dict(zip(
                    sector_flow["sector_name"],
                    sector_flow["rank"] if "rank" in sector_flow.columns
                    else range(1, len(sector_flow) + 1)
                ))
                df["sector_heat_rank"] = df["industry"].map(sector_rank_map)
                if df["sector_heat_rank"].isna().all():
                    df["sector_heat_rank"] = 50  # 默认中等

        # ---- 涨停板数据 ----
        limit_up = self.fetcher.fetch_limit_up(target_date)
        if not limit_up.empty and "code" in limit_up.columns:
            lu_cols = ["code", "consecutive", "seal_amount", "break_count", "industry"]
            lu_subset = limit_up[[c for c in lu_cols if c in limit_up.columns]]
            if "code" in lu_subset.columns:
                lu_subset = lu_subset.set_index("code")
                for c in lu_subset.columns:
                    if c not in df.columns:
                        df[c] = np.nan
                    df[c] = df[c].combine_first(lu_subset[c])

        # ---- 技术指标（批量获取K线，使用目标日期） ----
        # 为了效率，只获取候选池中前50支的K线
        top_codes = df.index[:50]
        indicators = self._batch_fetch_indicators(top_codes, target_date)
        if not indicators.empty:
            for col in indicators.columns:
                df[col] = indicators[col]
            # 用共通索引对齐
            df = df.combine_first(indicators)

        # 填充缺失值
        for col in df.columns:
            if df[col].dtype in [np.float64, np.int64, float, int]:
                df[col] = df[col].fillna(0)

        return df

    def _batch_fetch_indicators(self, codes: list[str], target_date: date) -> pd.DataFrame:
        """批量获取技术指标（获取截至 target_date 的K线）"""
        rows = []
        for code in codes[:40]:  # 限制40支
            try:
                kline = self.fetcher.fetch_history_kline(code, period=60, ref_date=target_date)
                if kline is None or kline.empty or len(kline) < 10:
                    continue

                latest = kline.iloc[-1]
                row = {"code": code}

                # 均线
                for ma in [5, 10, 20, 60]:
                    col = f"ma{ma}"
                    if col in kline.columns:
                        row[col] = latest[col]

                # MACD
                for macd_col in ["macd_dif", "macd_dea", "macd_bar"]:
                    if macd_col in kline.columns:
                        row[macd_col] = latest[macd_col]

                # RSI
                if "rsi" in kline.columns:
                    row["rsi"] = latest["rsi"]

                # 布林带
                for bb in ["boll_mid", "boll_upper", "boll_lower"]:
                    if bb in kline.columns:
                        row[bb] = latest[bb]

                # 量比
                if "volume_ratio_ind" in kline.columns:
                    row["volume_ratio_ind"] = latest["volume_ratio_ind"]

                # 换手率和流通市值 (从K线提取)
                if "turnover_rate" in kline.columns:
                    row["turnover_rate"] = latest["turnover_rate"]
                if "float_market_cap" in kline.columns:
                    row["float_market_cap"] = latest["float_market_cap"]

                rows.append(row)
            except Exception as e:
                logger.debug(f"获取 {code} K线失败: {e}")
                continue

        if not rows:
            return pd.DataFrame()

        result = pd.DataFrame(rows).set_index("code")
        return result

    def _dedup_by_sector(self, scored: pd.DataFrame) -> pd.DataFrame:
        """
        板块去重：同一板块/行业最多推荐 N 支。
        """
        max_same = self.cfg.get("max_same_sector", 2)
        if "industry" not in scored.columns:
            return scored

        kept = []
        sector_counts: dict[str, int] = {}

        for idx, row in scored.iterrows():
            sector = str(row.get("industry", "")).strip()
            if not sector or sector == "nan" or sector == "0":
                kept.append(idx)
                continue

            count = sector_counts.get(sector, 0)
            if count < max_same:
                kept.append(idx)
                sector_counts[sector] = count + 1

        return scored.loc[kept]
