"""
数据获取模块
统一封装 AkShare + 东方财富数据接口，含重试、缓存、降级。
"""
import logging
import time
from datetime import date, datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from .cache import get_cached, set_cache
from .calendar import get_last_trading_day

logger = logging.getLogger(__name__)

# ------------------------------
# 工具函数
# ------------------------------


def _retry(func, max_retries=3, backoff=2, **kwargs):
    """带指数退避的重试装饰器"""
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            return func(**kwargs)
        except Exception as e:
            last_err = e
            if attempt < max_retries:
                wait = backoff ** attempt
                logger.warning(f"{func.__name__} 第{attempt}次失败，{wait}秒后重试: {e}")
                time.sleep(wait)
    raise last_err


def _safe_float(val, default=np.nan) -> float:
    """安全转换为 float"""
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _clean_code(raw: str) -> str:
    """清洗股票代码为标准6位"""
    raw = str(raw).strip().zfill(6)
    return raw


# ------------------------------
# 数据获取类
# ------------------------------


class DataFetcher:
    """统一数据获取器"""

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.ttl = self.config.get("cache_ttl_hours", 4)
        self.timeout = self.config.get("request_timeout", 30)
        self.max_retries = self.config.get("max_retries", 3)
        self.backoff = self.config.get("retry_backoff", 2)

    # ========== 1. 龙虎榜明细 ==========

    def fetch_dragon_tiger(self, target_date: date | None = None) -> pd.DataFrame:
        """
        获取龙虎榜明细数据。
        返回字段: code, name, 净买额, 买入额, 卖出额, 上榜理由, 营业部信息
        """
        if target_date is None:
            target_date = get_last_trading_day()

        cache_key = {"date": target_date.isoformat()}
        cached = get_cached("dragon_tiger", self.ttl, **cache_key)
        if cached is not None:
            return cached

        try:
            # 尝试从 akshare 获取
            import akshare as ak
            df = ak.stock_lhb_detail_daily_sina(date=target_date.isoformat())
        except Exception:
            # 尝试东方财富接口
            df = self._fetch_dragon_tiger_eastmoney(target_date)

        if df is None or df.empty:
            logger.warning(f"龙虎榜数据为空: {target_date}")
            return pd.DataFrame()

        df = self._normalize_dragon_tiger(df, target_date)
        set_cache("dragon_tiger", df, **cache_key)
        logger.info(f"龙虎榜: {len(df)} 条记录 ({target_date})")
        return df

    def _fetch_dragon_tiger_eastmoney(self, target_date: date) -> pd.DataFrame | None:
        """从东方财富获取龙虎榜数据（备用）"""
        try:
            import akshare as ak
            # 尝试使用 stock_lhb_stock_detail_em
            df = ak.stock_lhb_stock_detail_em(date=target_date.isoformat().replace("-", ""))
            return df
        except Exception as e:
            logger.warning(f"东方财富龙虎榜接口也失败: {e}")
            return None

    def _normalize_dragon_tiger(self, df: pd.DataFrame, target_date: date) -> pd.DataFrame:
        """标准化龙虎榜数据格式"""
        # AkShare 返回格式可能不同，尝试自动识别列名
        col_map = {
            "代码": "code", "股票代码": "code", "symbol": "code",
            "名称": "name", "股票名称": "name",
            "净买额": "net_buy", "净买入额": "net_buy", "净买入": "net_buy",
            "买入额": "buy_amount", "总买入额": "buy_amount",
            "卖出额": "sell_amount", "总卖出额": "sell_amount",
            "上榜理由": "reason",
            "收盘价": "close",
            "涨跌幅": "pct_change",
        }
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

        # 确保必要列存在
        for col in ["code", "name", "net_buy", "buy_amount", "sell_amount"]:
            if col not in df.columns:
                df[col] = np.nan

        if "code" in df.columns:
            df["code"] = df["code"].astype(str).str.replace("sz", "").str.replace("sh", "").str.strip().str.zfill(6)

        df["date"] = target_date
        for c in ["net_buy", "buy_amount", "sell_amount"]:
            if c in df.columns:
                df[c] = df[c].apply(_safe_float)

        return df

    # ========== 2. 个股资金流向 ==========

    def fetch_capital_flow(self, target_date: date | None = None) -> pd.DataFrame:
        """
        获取全市场个股资金流向。
        优先东方财富接口，失败则用市场行情生成代理指标。
        """
        if target_date is None:
            target_date = get_last_trading_day()

        cache_key = {"date": target_date.isoformat()}
        cached = get_cached("capital_flow", self.ttl, **cache_key)
        if cached is not None:
            return cached

        df = None
        # 尝试东方财富接口
        try:
            import akshare as ak
            df = ak.stock_individual_fund_flow_rank(indicator="今日")
            df = self._normalize_capital_flow(df)
            logger.debug("资金流向: 东方财富")
        except Exception as e:
            logger.debug(f"东方财富资金流向不可用: {e}")

        # 备用：从行情数据生成代理指标
        if df is None or df.empty:
            logger.info("资金流向API不可用，使用量价代理指标")
            df = self._capital_flow_from_spot()

        if df is not None and not df.empty:
            set_cache("capital_flow", df, **cache_key)
            logger.info(f"资金流向: {len(df)} 条记录")
        else:
            df = pd.DataFrame()

        return df

    def _capital_flow_from_spot(self) -> pd.DataFrame:
        """从市场行情生成资金流向代理指标"""
        spot = self.fetch_market_spot()
        if spot is None or spot.empty:
            return pd.DataFrame()

        keep_cols = ["code", "name", "close", "pct_change", "volume", "amount",
                      "turnover_rate", "float_market_cap"]
        df = spot[[c for c in keep_cols if c in spot.columns]].copy()

        # 代理：成交额 * 涨跌幅方向 粗略估算资金关注度
        if "amount" in df.columns and "pct_change" in df.columns:
            df["main_net_inflow"] = df["amount"].fillna(0) * df["pct_change"].fillna(0) / 100
            df["main_net_inflow"] = df["main_net_inflow"].apply(_safe_float)
        else:
            df["main_net_inflow"] = 0.0

        for col in ["super_large_inflow", "large_inflow", "medium_inflow", "small_inflow"]:
            df[col] = 0.0

        return df

    def _fetch_capital_flow_by_market(self) -> pd.DataFrame | None:
        """按市场获取资金流向（备用）"""
        try:
            import akshare as ak
            dfs = []
            for market in ["沪A", "深A"]:
                try:
                    chunk = ak.stock_individual_fund_flow_rank(indicator="今日", market=market)
                    if chunk is not None and not chunk.empty:
                        dfs.append(chunk)
                except Exception:
                    pass
            if dfs:
                return pd.concat(dfs, ignore_index=True)
        except Exception as e:
            logger.warning(f"分市场资金流向失败: {e}")
        return None

    def _normalize_capital_flow(self, df: pd.DataFrame) -> pd.DataFrame:
        """标准化资金流向列名"""
        col_map = {
            "代码": "code", "股票代码": "code",
            "名称": "name", "股票名称": "name",
            "主力净流入": "main_net_inflow", "主力净流入-净额": "main_net_inflow",
            "超大单净流入-净额": "super_large_inflow",
            "大单净流入-净额": "large_inflow",
            "中单净流入-净额": "medium_inflow",
            "小单净流入-净额": "small_inflow",
            "涨跌幅": "pct_change", "最新价": "close",
            "换手率": "turnover_rate", "流通市值": "float_market_cap",
        }
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

        if "code" in df.columns:
            df["code"] = df["code"].astype(str).str.strip().str.zfill(6)

        money_cols = ["main_net_inflow", "super_large_inflow", "large_inflow", "medium_inflow", "small_inflow"]
        for c in money_cols:
            if c in df.columns:
                df[c] = df[c].apply(_safe_float)

        return df

    # ========== 3. 板块资金流向 ==========

    def fetch_sector_capital_flow(self) -> pd.DataFrame:
        """获取行业板块资金流向排名"""
        cache_key = {}
        cached = get_cached("sector_flow", self.ttl, **cache_key)
        if cached is not None:
            return cached

        df = None
        # 依次尝试不同的 sector_type 参数
        for sector_type in ["行业资金流向", "概念资金流向", "地域资金流向", None]:
            try:
                import akshare as ak
                if sector_type:
                    df = ak.stock_sector_fund_flow_rank(indicator="今日", sector_type=sector_type)
                else:
                    df = ak.stock_sector_fund_flow_rank(indicator="今日")
                if df is not None and not df.empty:
                    logger.debug(f"板块资金流向: sector_type={sector_type} 成功")
                    break
            except Exception:
                continue

        if df is None or df.empty:
            logger.warning("板块资金流向为空")
            return pd.DataFrame()

        col_map = {
            "名称": "sector_name", "板块名称": "sector_name",
            "主力净流入-净额": "main_net_inflow", "主力净流入": "main_net_inflow",
            "主力净流入-净占比": "main_net_ratio", "主力净占比": "main_net_ratio",
            "涨跌幅": "pct_change",
        }
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
        if "main_net_inflow" in df.columns:
            df["main_net_inflow"] = df["main_net_inflow"].apply(_safe_float)
        if "main_net_ratio" in df.columns:
            df["main_net_ratio"] = df["main_net_ratio"].apply(_safe_float)

        df["rank"] = range(1, len(df) + 1)
        set_cache("sector_flow", df, **cache_key)
        logger.info(f"板块资金流向: {len(df)} 个板块")
        return df

    def _fetch_sector_flow_fallback(self) -> pd.DataFrame | None:
        """备用：热点板块替代"""
        try:
            return self.fetch_hot_concepts()
        except Exception:
            return None

    # ========== 4. 热点概念板块 ==========

    def fetch_hot_concepts(self) -> pd.DataFrame:
        """获取市场热点概念板块排名"""
        cache_key = {}
        cached = get_cached("hot_concepts", 2, **cache_key)  # 热点变化快，2小时TTL
        if cached is not None:
            return cached

        try:
            import akshare as ak
            df = ak.stock_hot_rank_em()
        except Exception as e:
            logger.warning(f"热点概念获取失败: {e}")
            return pd.DataFrame()

        if df is None or df.empty:
            return pd.DataFrame()

        col_map = {
            "概念名称": "concept_name",
            "概念代码": "concept_code",
            "热度": "heat",
            "热度排名": "heat_rank",
            "相关股票": "related_stocks",
        }
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
        if "heat" in df.columns:
            df["heat"] = df["heat"].apply(_safe_float)

        set_cache("hot_concepts", df, **cache_key)
        logger.info(f"热点概念: {len(df)} 条")
        return df

    # ========== 5. 涨停板数据 ==========

    def fetch_limit_up(self, target_date: date | None = None) -> pd.DataFrame:
        """获取涨停板池数据"""
        if target_date is None:
            target_date = get_last_trading_day()

        cache_key = {"date": target_date.isoformat()}
        cached = get_cached("limit_up", self.ttl, **cache_key)
        if cached is not None:
            return cached

        try:
            import akshare as ak
            df = ak.stock_zt_pool_em(date=target_date.isoformat().replace("-", ""))
        except Exception as e:
            logger.warning(f"涨停板数据获取失败: {e}")
            return pd.DataFrame()

        if df is None or df.empty:
            return pd.DataFrame()

        col_map = {
            "代码": "code", "名称": "name",
            "涨停时间": "limit_time", "封板时间": "limit_time",
            "连板数": "consecutive", "连板天数": "consecutive",
            "封单金额": "seal_amount",
            "炸板次数": "break_count", "开板次数": "break_count",
            "所属行业": "industry",
            "涨停原因": "reason",
            "换手率": "turnover_rate",
            "流通市值": "float_market_cap",
            "最新价": "close",
            "涨跌幅": "pct_change",
        }
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

        if "code" in df.columns:
            df["code"] = df["code"].astype(str).str.strip().str.zfill(6)

        for c in ["seal_amount", "break_count", "consecutive", "turnover_rate"]:
            if c in df.columns:
                df[c] = df[c].apply(_safe_float)

        set_cache("limit_up", df, **cache_key)
        logger.info(f"涨停板: {len(df)} 支")
        return df

    # ========== 6. 北向资金 ==========

    def fetch_north_bound(self, days: int = 20) -> pd.DataFrame:
        """获取北向资金历史流向"""
        cache_key = {"days": days}
        cached = get_cached("north_bound", 8, **cache_key)  # 北向数据8小时TTL
        if cached is not None:
            return cached

        try:
            import akshare as ak
            df = ak.stock_hsgt_hist_em(symbol="北向资金")
        except Exception as e:
            logger.warning(f"北向资金获取失败: {e}")
            return pd.DataFrame()

        if df is None or df.empty:
            return pd.DataFrame()

        col_map = {
            "日期": "date", "净买入额": "net_inflow", "资金流向": "net_inflow",
        }
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

        if "net_inflow" in df.columns:
            df["net_inflow"] = df["net_inflow"].apply(_safe_float)

        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"]).dt.date

        df = df.tail(days)
        set_cache("north_bound", df, **cache_key)
        logger.info(f"北向资金: {len(df)} 天数据")
        return df

    # ========== 7. 全市场实时行情 ==========

    def fetch_market_spot(self) -> pd.DataFrame:
        """获取全市场A股实时行情（新浪数据源，东方财富备用）"""
        cache_key = {}
        cached = get_cached("market_spot", 1, **cache_key)  # 行情数据1小时TTL
        if cached is not None:
            return cached

        df = None
        # 优先使用新浪数据源（更稳定）
        try:
            import akshare as ak
            df = ak.stock_zh_a_spot()
            logger.debug("使用新浪数据源获取行情")
        except Exception as e:
            logger.debug(f"新浪行情失败: {e}")

        # 备用：东方财富
        if df is None or df.empty:
            try:
                import akshare as ak
                df = ak.stock_zh_a_spot_em()
                logger.debug("使用东方财富数据源获取行情")
            except Exception as e:
                logger.warning(f"全市场行情获取失败: {e}")
                return pd.DataFrame()

        if df is None or df.empty:
            return pd.DataFrame()

        # 新浪数据源的列名映射
        col_map = {
            # 新浪列名
            "代码": "code", "名称": "name",
            "最新价": "close", "今开": "open",
            "最高": "high", "最低": "low",
            "涨跌幅": "pct_change", "涨跌额": "change",
            "成交量": "volume", "成交额": "amount",
            "昨收": "prev_close",
            "买入": "bid", "卖出": "ask",
            # 东方财富列名（备用）
            "换手率": "turnover_rate", "量比": "volume_ratio",
            "市盈率-动态": "pe", "市净率": "pb",
            "流通市值": "float_market_cap", "总市值": "total_market_cap",
            "振幅": "amplitude",
        }
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

        if "code" in df.columns:
            df["code"] = df["code"].astype(str).str.strip()
            # 去掉交易所前缀 (sh/sz/bj)
            df["code"] = df["code"].str.replace("sh", "", regex=False)\
                                     .str.replace("sz", "", regex=False)\
                                     .str.replace("bj", "", regex=False)\
                                     .str.replace("SH", "", regex=False)\
                                     .str.replace("SZ", "", regex=False)\
                                     .str.replace("BJ", "", regex=False)\
                                     .str.strip().str.zfill(6)

        # 数值列清洗
        num_cols = ["close", "open", "high", "low", "prev_close",
                     "pct_change", "change", "volume", "amount",
                     "turnover_rate", "volume_ratio", "pe", "pb",
                     "float_market_cap", "total_market_cap", "amplitude",
                     "bid", "ask"]
        for c in num_cols:
            if c in df.columns:
                df[c] = df[c].apply(_safe_float)

        # 排除 ST
        if "name" in df.columns:
            df = df[~df["name"].astype(str).str.contains("ST|退", na=False)]

        set_cache("market_spot", df, **cache_key)
        logger.info(f"全市场行情: {len(df)} 支")
        return df

    # ========== 8. 个股历史K线 ==========

    @staticmethod
    def _code_with_prefix(code: str) -> str:
        """给股票代码加交易所前缀 (sh/sz/bj)"""
        code = str(code).strip().zfill(6)
        first = code[0]
        if first in ("0", "3"):
            return f"sz{code}"   # 深交所主板/创业板
        elif first == "6":
            return f"sh{code}"   # 上交所主板/科创板
        elif first in ("4", "8", "9"):
            return f"bj{code}"   # 北交所/新三板
        else:
            return f"sz{code}"

    def fetch_history_kline(self, code: str, period: int = 60,
                            freq: str = "daily", ref_date: date | None = None) -> pd.DataFrame:
        """
        获取个股历史K线数据。
        优先新浪源，备用腾讯源。

        Args:
            code: 股票6位代码
            period: 获取天数
            freq: 周期 daily
            ref_date: 参考日期（K线截至此日期），None=今天

        Returns:
            DataFrame with OHLCV, 均线, MACD, RSI
        """
        if ref_date is None:
            ref_date = date.today()

        cache_key = {"code": code, "period": period, "freq": freq, "ref": ref_date.isoformat()}
        cached = get_cached(f"kline_{freq}", self.ttl, **cache_key)
        if cached is not None:
            return cached

        df = None
        end_date = ref_date.isoformat()
        start_date = (ref_date - timedelta(days=period * 3)).isoformat()
        prefixed = self._code_with_prefix(code)

        # 方法1: 新浪日线 (adjust="" 避免 pandas 兼容问题)
        try:
            import akshare as ak
            df = ak.stock_zh_a_daily(
                symbol=prefixed, start_date=start_date,
                end_date=end_date, adjust=""
            )
            if df is not None and not df.empty:
                logger.debug(f"K线(新浪): {code} -> {len(df)} 条")
        except Exception as e:
            logger.debug(f"新浪K线失败 {code}: {e}")

        # 方法2: 腾讯源 (备用)
        if df is None or df.empty:
            try:
                import akshare as ak
                df = ak.stock_zh_a_hist_tx(
                    symbol=prefixed, start_date=start_date,
                    end_date=end_date
                )
                if df is not None and not df.empty:
                    logger.debug(f"K线(腾讯): {code} -> {len(df)} 条")
            except Exception as e:
                logger.debug(f"腾讯K线失败 {code}: {e}")

        # 方法3: 新浪带复权 (最后尝试)
        if df is None or df.empty:
            try:
                import akshare as ak
                df = ak.stock_zh_a_daily(
                    symbol=prefixed, start_date=start_date,
                    end_date=end_date, adjust="qfq"
                )
                if df is not None and not df.empty:
                    logger.debug(f"K线(新浪qfq): {code} -> {len(df)} 条")
            except Exception:
                pass

        if df is None or df.empty:
            return pd.DataFrame()

        # 统一列名（兼容中英文列名）
        col_map = {
            # 中文列名
            "日期": "date", "开盘": "open", "收盘": "close",
            "最高": "high", "最低": "low", "成交量": "volume",
            "成交额": "amount", "换手率": "turnover_rate",
            "涨跌幅": "pct_change", "振幅": "amplitude",
            # 英文列名（新浪 stock_zh_a_daily）
            "turnover": "turnover_rate",
            "outstanding_share": "outstanding_share",
        }
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

        for c in ["open", "close", "high", "low", "volume", "amount", "turnover_rate", "pct_change", "amplitude", "outstanding_share"]:
            if c in df.columns:
                df[c] = df[c].apply(_safe_float)

        # 计算流通市值（收盘价 * 流通股本 / 1亿 = 亿元）
        if "close" in df.columns and "outstanding_share" in df.columns:
            df["float_market_cap"] = df["close"] * df["outstanding_share"] / 1e8

        # 新浪换手率为小数（0.05 = 5%），统一转为百分比
        if "turnover_rate" in df.columns and df["turnover_rate"].max() < 10:
            df["turnover_rate"] = df["turnover_rate"] * 100

        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])

        df = df.tail(period).copy()

        # 计算技术指标
        if not df.empty and "close" in df.columns:
            df = self._add_indicators(df)

        set_cache(f"kline_{freq}", df, **cache_key)
        return df

    def _add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """为K线数据添加技术指标"""
        close = df["close"].values

        # 均线
        for ma in [5, 10, 20, 60]:
            if len(close) >= ma:
                df[f"ma{ma}"] = df["close"].rolling(ma).mean()

        # MACD
        if len(close) >= 26:
            ema12 = df["close"].ewm(span=12, adjust=False).mean()
            ema26 = df["close"].ewm(span=26, adjust=False).mean()
            df["macd_dif"] = ema12 - ema26
            df["macd_dea"] = df["macd_dif"].ewm(span=9, adjust=False).mean()
            df["macd_bar"] = 2 * (df["macd_dif"] - df["macd_dea"])

        # RSI
        if len(close) >= 14:
            delta = df["close"].diff()
            gain = delta.clip(lower=0)
            loss = (-delta).clip(lower=0)
            avg_gain = gain.ewm(alpha=1 / 14, adjust=False).mean()
            avg_loss = loss.ewm(alpha=1 / 14, adjust=False).mean()
            rs = avg_gain / avg_loss.replace(0, np.nan)
            df["rsi"] = 100 - (100 / (1 + rs))

        # 布林带
        if len(close) >= 20:
            df["boll_mid"] = df["close"].rolling(20).mean()
            std = df["close"].rolling(20).std()
            df["boll_upper"] = df["boll_mid"] + 2 * std
            df["boll_lower"] = df["boll_mid"] - 2 * std

        # 量比（当日成交量 / 5日均量）
        if "volume" in df.columns and len(df) >= 5:
            avg_vol = df["volume"].rolling(5).mean().shift(1)
            df["volume_ratio_ind"] = df["volume"] / avg_vol.replace(0, np.nan)

        return df

    # ========== 批量获取 ==========

    def fetch_all_short_term_data(self, target_date: date | None = None) -> dict[str, pd.DataFrame]:
        """
        一次性获取短线分析需要的所有数据。
        并行请求（未来可用 asyncio 优化），目前顺序获取。
        """
        logger.info("=" * 50)
        logger.info(f"开始获取短线数据... (日期: {target_date or get_last_trading_day()})")

        results = {
            "dragon_tiger": self.fetch_dragon_tiger(target_date),
            "capital_flow": self.fetch_capital_flow(target_date),
            "sector_flow": self.fetch_sector_capital_flow(),
            "hot_concepts": self.fetch_hot_concepts(),
            "limit_up": self.fetch_limit_up(target_date),
            "north_bound": self.fetch_north_bound(),
            "market_spot": self.fetch_market_spot(),
        }

        logger.info("数据获取完成")
        return results
