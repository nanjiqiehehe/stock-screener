"""
多因子加权评分引擎

核心设计：
- 每项因子得分 0-100，按配置权重加权求和
- 得分归一化：同维度内用百分位排名 + Z-score 混合
- 缺失值处理：缺失因子给 0 分但不影响其他因子
"""
import logging
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class FactorScorer:
    """因子评分的基类，提供通用方法"""

    @staticmethod
    def pct_rank(series: pd.Series) -> pd.Series:
        """百分位排名得分（0-100），处理极值和 NaN"""
        result = series.rank(pct=True, na_option="bottom") * 100
        return result.fillna(0)

    @staticmethod
    def zscore(series: pd.Series, cap: float = 3.0) -> pd.Series:
        """Z-Score 标准化后映射到 0-100，超出 ±3σ 截尾"""
        mean = series.mean()
        std = series.std()
        if std == 0 or pd.isna(std):
            return pd.Series(50.0, index=series.index)
        z = (series - mean) / std
        z = z.clip(-cap, cap)
        return (z + cap) / (2 * cap) * 100

    @staticmethod
    def binary_score(condition: pd.Series) -> pd.Series:
        """二值评分：满足条件=100，否则=0"""
        return condition.astype(float) * 100

    @staticmethod
    def minmax(series: pd.Series) -> pd.Series:
        """Min-Max 归一化到 0-100"""
        mn, mx = series.min(), series.max()
        if mx == mn or pd.isna(mx) or pd.isna(mn):
            return pd.Series(50.0, index=series.index)
        return ((series - mn) / (mx - mn)) * 100

    @staticmethod
    def range_score(series: pd.Series, low: float, high: float,
                    peak: float | None = None) -> pd.Series:
        """
        区间评分：在 [low, high] 内的值得分高，两端递减。
        peak 为最佳值，默认为区间中点。
        """
        if peak is None:
            peak = (low + high) / 2

        def _score(x):
            if pd.isna(x):
                return 0
            if x < low:
                # 低于下限：线性衰减到0（最低衰减到0）
                dist = (low - x) / max(low, 1)
                return max(0, 100 - dist * 50)
            elif x > high:
                # 高于上限：线性衰减
                dist = (x - high) / max(high, 1)
                return max(0, 100 - dist * 50)
            else:
                # 在区间内：离 peak 越近分越高
                dist_from_peak = abs(x - peak) / max((high - low) / 2, 0.01)
                return 100 - dist_from_peak * 30

        return series.apply(_score)

    @staticmethod
    def weighted_sum(scores: dict[str, pd.Series],
                     weights: dict[str, float]) -> pd.Series:
        """加权求和各因子得分"""
        total_weight = sum(weights.values())
        if total_weight == 0:
            return pd.Series(0, index=list(scores.values())[0].index)

        result = pd.Series(0.0, index=list(scores.values())[0].index)
        for name, series in scores.items():
            w = weights.get(name, 0)
            if w > 0 and series is not None:
                result += series.fillna(0) * w / total_weight
        return result


class ShortTermScorer:
    """短线多因子评分器"""

    def __init__(self, config: dict):
        self.config = config.get("short_term", {})
        self.w = self.config.get("weights", {})
        self.w_sub = {
            "capital_flow": self.config.get("capital_flow_sub", {}),
            "sentiment": self.config.get("sentiment_sub", {}),
            "technical": self.config.get("technical_sub", {}),
            "liquidity": self.config.get("liquidity_sub", {}),
        }
        self.filter_cfg = self.config.get("filter", {})

    def score(self, merged: pd.DataFrame) -> pd.DataFrame:
        """
        对合并后的股票数据打分。

        Args:
            merged: 包含资金、情绪、技术、流通各维度字段的 DataFrame
                    索引为 code

        Returns:
            新增 score_capital, score_sentiment, score_technical,
            score_liquidity, total_score 列的 DataFrame
        """
        if merged.empty:
            return merged

        result = merged.copy()

        # ===== 1. 资金面 =====
        capital_scores = {}

        # 主力净流入
        if "main_net_inflow" in result.columns:
            capital_scores["main_force_inflow"] = FactorScorer.zscore(
                result["main_net_inflow"].fillna(0)
            )

        # 龙虎榜净买入
        if "dragon_tiger_net" in result.columns:
            capital_scores["dragon_tiger_net"] = FactorScorer.zscore(
                result["dragon_tiger_net"].fillna(0)
            )

        # 游资参与度（龙虎榜中游资营业部占比）
        if "hot_money_ratio" in result.columns:
            capital_scores["hot_money_activity"] = FactorScorer.minmax(
                result["hot_money_ratio"].fillna(0)
            )
        else:
            capital_scores["hot_money_activity"] = pd.Series(0, index=result.index)

        # 北向增持（近N日净流入为正）
        if "north_flow_positive" in result.columns:
            capital_scores["north_bound_increase"] = FactorScorer.binary_score(
                result["north_flow_positive"]
            )
        else:
            capital_scores["north_bound_increase"] = pd.Series(0, index=result.index)

        result["score_capital"] = FactorScorer.weighted_sum(
            capital_scores, self.w_sub.get("capital_flow", {})
        )

        # ===== 2. 情绪面 =====
        sentiment_scores = {}

        # 涨停强度（涨停板排名越靠前/封单越大）
        if "seal_amount" in result.columns and "consecutive" in result.columns:
            # 连板高度得分
            sentiment_scores["consecutive_boards"] = FactorScorer.minmax(
                result["consecutive"].fillna(0)
            )
            # 封单强度
            sentiment_scores["limit_up_strength"] = FactorScorer.zscore(
                result["seal_amount"].fillna(0)
            )
        else:
            sentiment_scores["limit_up_strength"] = pd.Series(0, index=result.index)
            sentiment_scores["consecutive_boards"] = pd.Series(0, index=result.index)

        # 板块热度
        if "sector_heat_rank" in result.columns:
            sentiment_scores["sector_heat"] = pd.Series(
                np.maximum(0, 100 - result["sector_heat_rank"].fillna(100)),
                index=result.index
            )
        else:
            sentiment_scores["sector_heat"] = pd.Series(0, index=result.index)

        result["score_sentiment"] = FactorScorer.weighted_sum(
            sentiment_scores, self.w_sub.get("sentiment", {})
        )

        # ===== 3. 技术面 =====
        technical_scores = {}

        # 均线多头排列
        if all(c in result.columns for c in ["ma5", "ma10", "ma20", "close"]):
            bullish = (
                (result["close"] > result["ma5"]) &
                (result["ma5"] > result["ma10"]) &
                (result["ma10"] > result["ma20"])
            )
            technical_scores["ma_bullish"] = FactorScorer.binary_score(bullish)
        elif "close" in result.columns and "ma5" in result.columns:
            technical_scores["ma_bullish"] = FactorScorer.binary_score(
                result["close"] > result["ma5"]
            )
        else:
            technical_scores["ma_bullish"] = pd.Series(0, index=result.index)

        # MACD 信号
        if all(c in result.columns for c in ["macd_dif", "macd_dea", "macd_bar"]):
            # DIF > DEA 且 红柱放大
            macd_bull = (
                (result["macd_dif"] > result["macd_dea"]) &
                (result["macd_bar"] > 0)
            )
            technical_scores["macd_signal"] = FactorScorer.binary_score(macd_bull)
        else:
            technical_scores["macd_signal"] = pd.Series(0, index=result.index)

        # 量价配合（放量上涨）
        if all(c in result.columns for c in ["pct_change", "volume_ratio_ind"]):
            vol_price = (
                (result["pct_change"] > 2) &
                (result["volume_ratio_ind"] > 1.2)
            )
            technical_scores["volume_price"] = FactorScorer.binary_score(vol_price)
        elif "volume_ratio" in result.columns and "pct_change" in result.columns:
            vol_price = (
                (result["pct_change"] > 2) &
                (result["volume_ratio"] > 1.2)
            )
            technical_scores["volume_price"] = FactorScorer.binary_score(vol_price)
        else:
            technical_scores["volume_price"] = pd.Series(0, index=result.index)

        # RSI 强势区间 (50-80)
        if "rsi" in result.columns:
            rsi_ok = (result["rsi"] >= 50) & (result["rsi"] <= 80)
            rsi_score = result["rsi"].copy()
            rsi_score[~rsi_ok] = 30  # 超出区间给低分
            rsi_score[rsi_ok] = FactorScorer.range_score(
                rsi_score[rsi_ok], 50, 80, 65
            )
            technical_scores["rsi_range"] = rsi_score
        else:
            technical_scores["rsi_range"] = pd.Series(0, index=result.index)

        result["score_technical"] = FactorScorer.weighted_sum(
            technical_scores, self.w_sub.get("technical", {})
        )

        # ===== 4. 流通性 =====
        liquidity_scores = {}

        # 流通市值适中（20亿-500亿，最优100-200亿）
        if "float_market_cap" in result.columns:
            # float_market_cap 单位可能是亿或元
            cap = result["float_market_cap"].copy()
            # 如果数值很大（>10000），可能是以元为单位，转换为亿
            if cap.median() > 10000:
                cap = cap / 1e8
            liquidity_scores["market_cap_fit"] = FactorScorer.range_score(
                cap, 20, 500, 150
            )
        else:
            liquidity_scores["market_cap_fit"] = pd.Series(0, index=result.index)

        # 换手率健康（5%-25%）
        if "turnover_rate" in result.columns:
            liquidity_scores["turnover_healthy"] = FactorScorer.range_score(
                result["turnover_rate"].fillna(0),
                self.filter_cfg.get("min_turnover_rate", 5),
                self.filter_cfg.get("max_turnover_rate", 25),
                12,
            )
        else:
            liquidity_scores["turnover_healthy"] = pd.Series(0, index=result.index)

        # 振幅合理
        if "amplitude" in result.columns:
            liquidity_scores["amplitude_reasonable"] = FactorScorer.range_score(
                result["amplitude"].fillna(0), 3, 12, 7
            )
        else:
            liquidity_scores["amplitude_reasonable"] = pd.Series(0, index=result.index)

        result["score_liquidity"] = FactorScorer.weighted_sum(
            liquidity_scores, self.w_sub.get("liquidity", {})
        )

        # ===== 综合得分 =====
        result["total_score"] = (
            result["score_capital"] * self.w.get("capital_flow", 30) / 100 +
            result["score_sentiment"] * self.w.get("sentiment", 25) / 100 +
            result["score_technical"] * self.w.get("technical", 25) / 100 +
            result["score_liquidity"] * self.w.get("liquidity", 20) / 100
        )

        return result.sort_values("total_score", ascending=False)


class MidTermScorer:
    """中线多因子评分器"""

    def __init__(self, config: dict):
        self.config = config.get("mid_term", {})
        self.w = self.config.get("weights", {})
        self.w_sub = {
            "trend": self.config.get("trend_sub", {}),
            "fundamentals": self.config.get("fundamentals_sub", {}),
            "capital_accumulation": self.config.get("capital_accumulation_sub", {}),
            "industry_prosperity": self.config.get("industry_prosperity_sub", {}),
        }

    def score(self, merged: pd.DataFrame) -> pd.DataFrame:
        """中线评分"""
        if merged.empty:
            return merged

        result = merged.copy()

        # ===== 1. 趋势面 =====
        trend_scores = {}

        # 周线/月线多头
        if all(c in result.columns for c in ["close", "ma20", "ma60"]):
            trend_scores["weekly_bullish"] = FactorScorer.binary_score(
                (result["close"] > result["ma20"]) &
                (result["close"] > result["ma60"]) &
                (result["ma20"] > result["ma60"])
            )
        else:
            trend_scores["weekly_bullish"] = pd.Series(0, index=result.index)

        # 中期均线斜率
        if "ma60_slope" in result.columns:
            trend_scores["ma_slope"] = FactorScorer.zscore(
                result["ma60_slope"].fillna(0)
            )
        else:
            trend_scores["ma_slope"] = pd.Series(0, index=result.index)

        # 布林带位置（中轨上方）
        if all(c in result.columns for c in ["close", "boll_mid", "boll_upper"]):
            bb_pos = (result["close"] - result["boll_mid"]) / (
                result["boll_upper"] - result["boll_mid"] + 0.01
            )
            # 0.3-0.8 之间较理想
            trend_scores["bollinger_position"] = FactorScorer.range_score(
                bb_pos.fillna(0), 0.2, 0.85, 0.5
            )
        else:
            trend_scores["bollinger_position"] = pd.Series(0, index=result.index)

        result["score_trend"] = FactorScorer.weighted_sum(
            trend_scores, self.w_sub.get("trend", {})
        )

        # ===== 2. 基本面 =====
        fundamental_scores = {}

        # ROE
        if "roe" in result.columns:
            fundamental_scores["roe"] = FactorScorer.range_score(
                result["roe"].fillna(0), 8, 30, 15
            )
        else:
            fundamental_scores["roe"] = pd.Series(50, index=result.index)

        # 营收增速
        if "revenue_growth" in result.columns:
            fundamental_scores["revenue_growth"] = FactorScorer.range_score(
                result["revenue_growth"].fillna(0), 10, 50, 25
            )
        else:
            fundamental_scores["revenue_growth"] = pd.Series(50, index=result.index)

        # PE分位（越低越好）
        if "pe" in result.columns:
            pe = result["pe"].fillna(result["pe"].median())
            # PE > 0, 取倒数做排序（PE越低分越高）
            pe_clean = pe.clip(lower=1, upper=500)
            fundamental_scores["pe_percentile"] = 100 - FactorScorer.pct_rank(pe_clean)
        else:
            fundamental_scores["pe_percentile"] = pd.Series(50, index=result.index)

        result["score_fundamentals"] = FactorScorer.weighted_sum(
            fundamental_scores, self.w_sub.get("fundamentals", {})
        )

        # ===== 3. 资金沉淀 =====
        capital_acc_scores = {}

        # 机构持仓变化（用北向连续流入天数模拟）
        if "north_flow_days" in result.columns:
            capital_acc_scores["north_flow_continuous"] = FactorScorer.minmax(
                result["north_flow_days"].fillna(0)
            )
        else:
            capital_acc_scores["north_flow_continuous"] = pd.Series(0, index=result.index)

        if "institution_change" in result.columns:
            capital_acc_scores["institution_change"] = FactorScorer.zscore(
                result["institution_change"].fillna(0)
            )
        else:
            capital_acc_scores["institution_change"] = pd.Series(0, index=result.index)

        result["score_capital_acc"] = FactorScorer.weighted_sum(
            capital_acc_scores, self.w_sub.get("capital_accumulation", {})
        )

        # ===== 4. 行业景气 =====
        industry_scores = {}

        if "sector_relative_strength" in result.columns:
            industry_scores["relative_strength"] = FactorScorer.zscore(
                result["sector_relative_strength"].fillna(0)
            )
        else:
            industry_scores["relative_strength"] = pd.Series(50, index=result.index)

        industry_scores["policy_support"] = pd.Series(50, index=result.index)

        result["score_industry"] = FactorScorer.weighted_sum(
            industry_scores, self.w_sub.get("industry_prosperity", {})
        )

        # ===== 综合得分 =====
        result["total_score"] = (
            result["score_trend"] * self.w.get("trend", 35) / 100 +
            result["score_fundamentals"] * self.w.get("fundamentals", 30) / 100 +
            result["score_capital_acc"] * self.w.get("capital_accumulation", 20) / 100 +
            result["score_industry"] * self.w.get("industry_prosperity", 15) / 100
        )

        return result.sort_values("total_score", ascending=False)
