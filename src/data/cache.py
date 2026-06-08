"""
数据缓存模块
- 以 pickle 格式将 DataFrame 缓存到本地
- 支持 TTL 过期自动刷新
"""
import hashlib
import logging
import pickle
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

# 缓存目录
CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_key(prefix: str, **kwargs) -> str:
    """生成缓存文件名（基于参数哈希）"""
    raw = f"{prefix}_{sorted(kwargs.items())}"
    h = hashlib.md5(raw.encode()).hexdigest()[:12]
    return f"{prefix}_{h}.pkl"


def get_cached(prefix: str, ttl_hours: int = 4, **kwargs) -> pd.DataFrame | None:
    """
    读取缓存数据。

    Args:
        prefix: 缓存前缀（如 'dragon_tiger', 'capital_flow'）
        ttl_hours: 缓存有效小时数
        **kwargs: 区分缓存的参数

    Returns:
        DataFrame 或 None（缓存未命中/已过期）
    """
    fname = _cache_key(prefix, **kwargs)
    fpath = CACHE_DIR / fname

    if not fpath.exists():
        return None

    try:
        with open(fpath, "rb") as f:
            ts, df = pickle.load(f)

        age = datetime.now() - ts
        if age > timedelta(hours=ttl_hours):
            logger.debug(f"缓存过期: {fname} (age={age})")
            fpath.unlink(missing_ok=True)
            return None

        logger.debug(f"缓存命中: {fname} (age={age})")
        return df
    except Exception as e:
        logger.warning(f"读取缓存失败: {e}")
        fpath.unlink(missing_ok=True)
        return None


def set_cache(prefix: str, df: pd.DataFrame, **kwargs) -> None:
    """
    写入缓存。

    Args:
        prefix: 缓存前缀
        df: 要缓存的数据
        **kwargs: 区分缓存的参数
    """
    fname = _cache_key(prefix, **kwargs)
    fpath = CACHE_DIR / fname

    try:
        with open(fpath, "wb") as f:
            pickle.dump((datetime.now(), df), f)
        logger.debug(f"缓存写入: {fname}")
    except Exception as e:
        logger.warning(f"写入缓存失败: {e}")


def clear_cache(prefix: str | None = None) -> int:
    """
    清理缓存。

    Args:
        prefix: 指定前缀清理，None 则清空全部

    Returns:
        清理的文件数
    """
    count = 0
    pattern = f"{prefix}_*.pkl" if prefix else "*.pkl"
    for f in CACHE_DIR.glob(pattern):
        f.unlink()
        count += 1
    logger.info(f"清理了 {count} 个缓存文件")
    return count
