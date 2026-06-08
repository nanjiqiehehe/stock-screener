"""
交易日历模块
判断当前日期是否为A股交易日，提供最近交易日查询。
数据来源：akshare 交易日历
"""
import logging
from datetime import date, datetime, timedelta
from functools import lru_cache

logger = logging.getLogger(__name__)


class TradingCalendar:
    """A股交易日历"""

    def __init__(self):
        self._trade_dates: set | None = None

    def _load_trade_calendar(self, year: int | None = None) -> set:
        """从 akshare 加载交易日历并缓存"""
        if year is None:
            year = date.today().year

        try:
            import akshare as ak
            df = ak.tool_trade_date_hist_sina()
            # 列名可能为 trade_date，类型为 str "YYYY-MM-DD"
            col = df.columns[0]
            dates = set(
                date.fromisoformat(d)
                for d in df[col].astype(str).str[:10]
            )
            self._trade_dates = dates
            logger.info(f"已加载 {len(dates)} 个交易日")
            return dates
        except Exception as e:
            logger.warning(f"加载交易日历失败: {e}，使用简易判断")
            return self._fallback_trade_dates(year)

    @staticmethod
    def _fallback_trade_dates(year: int) -> set:
        """简易判断：排除周末 + 固定节假日"""
        all_days = set()
        start = date(year, 1, 1)
        end = date(year, 12, 31)
        current = start
        while current <= end:
            # 排除周六周日
            if current.weekday() < 5:
                all_days.add(current)
            current += timedelta(days=1)

        # 排除常见节假日（简化版）
        holidays = {
            date(year, 1, 1),  # 元旦
            date(year, 1, 2),
            date(year, 1, 3),
            date(year, 5, 1),  # 劳动节
            date(year, 5, 2),
            date(year, 5, 3),
            date(year, 10, 1),  # 国庆
            date(year, 10, 2),
            date(year, 10, 3),
            date(year, 10, 4),
            date(year, 10, 5),
            date(year, 10, 6),
            date(year, 10, 7),
        }
        # 春节（粗略估算 1 月底到 2 月初的 7 天）
        for d in _chinese_new_year_approx(year):
            holidays.add(d)

        return all_days - holidays

    @property
    def trade_dates(self) -> set:
        """获取已缓存的交易日集合"""
        if self._trade_dates is None:
            self._load_trade_calendar()
        return self._trade_dates

    def is_trading_day(self, d: date | None = None) -> bool:
        """判断是否为交易日"""
        if d is None:
            d = date.today()
        # 如果日期不在加载的年份中，重新加载
        if d.year not in {td.year for td in self.trade_dates}:
            self._load_trade_calendar(d.year)
        return d in self.trade_dates

    def get_last_trading_day(self, d: date | None = None) -> date:
        """获取最近的交易日（含当天）"""
        if d is None:
            d = date.today()
        cursor = d
        for _ in range(30):  # 最多往回找30天
            if self.is_trading_day(cursor):
                return cursor
            cursor -= timedelta(days=1)
        return d  # fallback

    def get_next_trading_day(self, d: date | None = None) -> date:
        """获取下一个交易日"""
        if d is None:
            d = date.today()
        cursor = d + timedelta(days=1)
        for _ in range(30):
            if self.is_trading_day(cursor):
                return cursor
            cursor += timedelta(days=1)
        return d  # fallback


def _chinese_new_year_approx(year: int) -> list[date]:
    """粗略估计春节日期（简化版，覆盖 ±3 天误差范围）"""
    # 基于历史数据的粗略估算
    cny_map = {
        2024: date(2024, 2, 10),
        2025: date(2025, 1, 29),
        2026: date(2026, 2, 17),
        2027: date(2027, 2, 6),
        2028: date(2028, 1, 26),
    }
    base = cny_map.get(year, date(year, 2, 1))
    holidays = []
    for i in range(-1, 6):  # 除夕到初五
        holidays.append(base + timedelta(days=i))
    return holidays


# 全局单例
_calendar_instance: TradingCalendar | None = None


def get_calendar() -> TradingCalendar:
    """获取交易日历单例"""
    global _calendar_instance
    if _calendar_instance is None:
        _calendar_instance = TradingCalendar()
    return _calendar_instance


def is_trading_day(d: date | None = None) -> bool:
    """便捷函数：判断是否为交易日"""
    return get_calendar().is_trading_day(d)


def get_last_trading_day(d: date | None = None) -> date:
    """便捷函数：获取最近交易日"""
    return get_calendar().get_last_trading_day(d)


def get_next_trading_day(d: date | None = None) -> date:
    """便捷函数：获取下一个交易日"""
    return get_calendar().get_next_trading_day(d)
