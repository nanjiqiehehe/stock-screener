"""
报告生成模块

输出格式：
1. Rich 终端彩色表格（实时查看）
2. Markdown 文件（存档，保存到 reports/ 目录）
"""
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


class Reporter:
    """报告生成器"""

    def __init__(self, config: dict):
        self.cfg = config.get("report", {})
        self.output_dir = Path(self.cfg.get("output_dir", "reports"))
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.save_md = self.cfg.get("save_markdown", True)
        self.show_rich = self.cfg.get("show_rich_table", True)
        self.show_buy_range = self.cfg.get("show_buy_range", True)
        self.show_stop_loss = self.cfg.get("show_stop_loss", True)
        self.stop_loss_pct = self.cfg.get("stop_loss_pct", -5.0)

    def generate(
        self,
        short_term: pd.DataFrame,
        mid_term: pd.DataFrame,
        target_date: Optional[date] = None,
        market_summary: Optional[dict] = None,
    ) -> str:
        """
        生成完整报告。

        Args:
            short_term: 短线推荐结果
            mid_term: 中线推荐结果
            target_date: 报告日期
            market_summary: 市场概况（可选）

        Returns:
            Markdown 格式的报告内容
        """
        if target_date is None:
            target_date = date.today()

        md = self._build_markdown(short_term, mid_term, target_date, market_summary)

        # 保存文件
        if self.save_md:
            fname = self.output_dir / f"{target_date.isoformat()}.md"
            fname.write_text(md, encoding="utf-8")
            logger.info(f"报告已保存: {fname}")

        # 终端展示
        if self.show_rich:
            self._render_rich(short_term, mid_term, target_date)

        return md

    def _build_markdown(
        self,
        short_term: pd.DataFrame,
        mid_term: pd.DataFrame,
        target_date: date,
        market_summary: Optional[dict] = None,
    ) -> str:
        """构建 Markdown 报告"""
        lines = []
        lines.append(f"# 🔬 A股智能选股日报")
        lines.append(f"")
        lines.append(f"**日期**: {target_date.isoformat()}  {target_date.strftime('%A')}")
        lines.append(f"**生成时间**: {datetime.now().strftime('%H:%M:%S')}")
        lines.append(f"")
        lines.append("---")

        # 市场概况
        if market_summary:
            lines.append(f"## 📊 市场概况")
            lines.append(f"")
            for k, v in market_summary.items():
                lines.append(f"- **{k}**: {v}")
            lines.append(f"")

        # 短线推荐
        lines.append(f"## ⚡ 短线精选 (Top {len(short_term)})")
        lines.append(f"")
        lines.append(f"> 基于资金面、情绪面、技术面、流通性四维量化评分")
        lines.append(f"")

        if short_term.empty:
            lines.append("⚠️ *今日无短线推荐（无股票通过筛选条件）*")
        else:
            lines.append(f"| # | 代码 | 名称 | 总分 | 资金 | 情绪 | 技术 | 流通 | 入选逻辑 |")
            lines.append(f"|---|------|------|------|------|------|------|------|----------|")
            for i, (idx, row) in enumerate(short_term.iterrows(), 1):
                code = row.get("code", idx)
                name = row.get("name", "")
                total = self._fmt(row.get("total_score", 0))
                cap = self._fmt(row.get("score_capital", 0))
                sent = self._fmt(row.get("score_sentiment", 0))
                tech = self._fmt(row.get("score_technical", 0))
                liq = self._fmt(row.get("score_liquidity", 0))
                reason = self._build_reason(row)
                lines.append(f"| {i} | {code} | {name} | {total} | {cap} | {sent} | {tech} | {liq} | {reason} |")

            lines.append(f"")

            # 详细分析
            lines.append(f"### 📋 短线个股详情")
            lines.append(f"")
            for i, (idx, row) in enumerate(short_term.iterrows(), 1):
                code = row.get("code", idx)
                name = row.get("name", "")
                close = row.get("close", "N/A")
                pct = row.get("pct_change", 0)
                turnover = row.get("turnover_rate", "N/A")
                industry = row.get("industry", "N/A")
                consecutive = row.get("consecutive", 0)

                lines.append(f"#### {i}. {name} (`{code}`)")
                lines.append(f"")
                lines.append(f"| 指标 | 数值 |")
                lines.append(f"|------|------|")
                lines.append(f"| 最新价 | {close} |")
                lines.append(f"| 涨跌幅 | {pct:+.2f}% |" if isinstance(pct, (int, float)) else f"| 涨跌幅 | {pct} |")
                lines.append(f"| 换手率 | {turnover}% |" if isinstance(turnover, (int, float)) else f"| 换手率 | {turnover} |")
                lines.append(f"| 所属行业 | {industry} |")
                if consecutive and consecutive > 0:
                    lines.append(f"| 连板数 | {int(consecutive)} |")

                # 买入区间与止损
                if self.show_buy_range and isinstance(close, (int, float)) and isinstance(pct, (int, float)):
                    buy_low = round(close * 0.98, 2)
                    buy_high = round(close * 1.03, 2)
                    lines.append(f"| 建议买入区间 | {buy_low} ~ {buy_high} |")

                if self.show_stop_loss and isinstance(close, (int, float)):
                    stop = round(close * (1 + self.stop_loss_pct / 100), 2)
                    lines.append(f"| 止损位 | {stop} ({self.stop_loss_pct}%) |")

                lines.append(f"")

        # 中线推荐
        lines.append(f"---")
        lines.append(f"## 🏔️ 中线趋势股 (Top {len(mid_term)})")
        lines.append(f"")
        lines.append(f"> 基于趋势面、基本面、资金沉淀、行业景气四维量化评分")
        lines.append(f"> 持有周期：3-6个月 | 仅分数达标（≥{self.cfg.get('report',{}).get('min_score',65) if False else 65}）才推荐")
        lines.append(f"")

        if mid_term.empty:
            lines.append("💤 *今日无中线推荐（无股票达到评分阈值）*")
            lines.append(f"")
            lines.append(f"> 中线选股宁缺毋滥——只有趋势+基本面+资金面同时共振时才会触发推荐。")
        else:
            lines.append(f"| # | 代码 | 名称 | 总分 | 趋势 | 基本面 | 资金沉淀 | 行业景气 | 核心理由 |")
            lines.append(f"|---|------|------|------|------|--------|----------|----------|----------|")
            for i, (idx, row) in enumerate(mid_term.iterrows(), 1):
                code = row.get("code", idx)
                name = row.get("name", "")
                total = self._fmt(row.get("total_score", 0))
                trend_s = self._fmt(row.get("score_trend", 0))
                fund_s = self._fmt(row.get("score_fundamentals", 0))
                cap_s = self._fmt(row.get("score_capital_acc", 0))
                ind_s = self._fmt(row.get("score_industry", 0))
                reason = self._build_mid_reason(row)
                lines.append(f"| {i} | {code} | {name} | {total} | {trend_s} | {fund_s} | {cap_s} | {ind_s} | {reason} |")

        lines.append(f"")
        lines.append(f"---")
        lines.append(f"")
        lines.append(f"⚠️ **风险提示**: 本报告为量化模型辅助研究工具产出，不构成投资建议。股市有风险，投资需谨慎。所有交易决策请自行判断，风险自担。")
        lines.append(f"")
        lines.append(f"📅 报告日期: {target_date.isoformat()} | 🤖 由 A股智能选股助手自动生成")

        return "\n".join(lines)

    @staticmethod
    def _fmt(val) -> str:
        """格式化数值"""
        if pd.isna(val) or val is None:
            return "-"
        try:
            return f"{float(val):.1f}"
        except (ValueError, TypeError):
            return str(val)

    def _build_reason(self, row) -> str:
        """根据行数据构建一句话入选理由"""
        reasons = []
        name = row.get("name", "")
        pct = row.get("pct_change", 0)
        consecutive = row.get("consecutive", 0)

        if consecutive and consecutive >= 2:
            reasons.append(f"{int(consecutive)}连板")
        if isinstance(pct, (int, float)) and pct >= 9:
            reasons.append("涨停")
        elif isinstance(pct, (int, float)) and pct >= 5:
            reasons.append("强势上涨")

        # 看资金面得分
        cap_score = row.get("score_capital", 0)
        if cap_score >= 65:
            reasons.append("主力资金流入")

        # 龙虎榜
        dt_net = row.get("dragon_tiger_net", 0)
        if dt_net and dt_net > 0:
            reasons.append("龙虎榜净买入")

        return " + ".join(reasons) if reasons else "多因子共振"

    def _build_mid_reason(self, row) -> str:
        """中线入选理由"""
        reasons = []

        if row.get("score_trend", 0) >= 65:
            reasons.append("趋势多头排列")
        if row.get("score_fundamentals", 0) >= 60:
            reasons.append("基本面优良")
        if row.get("north_flow_days", 0) >= 10:
            reasons.append("北向持续流入")
        if row.get("score_industry", 0) >= 55:
            reasons.append("行业景气度好")

        return " + ".join(reasons) if reasons else "中长期配置价值"

    def _render_plain(self, short_term, mid_term, target_date):
        """纯文本降级输出（Windows GBK兼容）"""
        logger = logging.getLogger(__name__)
        logger.info(f"\n{'='*50}")
        logger.info(f"A股智能选股日报 — {target_date.isoformat()}")
        logger.info(f"{'='*50}")

        if hasattr(short_term, 'empty') and not short_term.empty:
            logger.info(f"\n[短线精选] Top {len(short_term)}:")
            for i, (idx, row) in enumerate(short_term.iterrows(), 1):
                name = row.get("name", "")
                code = row.get("code", idx)
                score = self._fmt(row.get("total_score", 0))
                pct = row.get("pct_change", 0)
                pct_s = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else "-"
                logger.info(f"  {i}. {name} ({code}) 评分:{score} 涨跌:{pct_s}")

        if hasattr(mid_term, 'empty') and not mid_term.empty:
            logger.info(f"\n[中线趋势] Top {len(mid_term)}:")
            for i, (idx, row) in enumerate(mid_term.iterrows(), 1):
                name = row.get("name", "")
                code = row.get("code", idx)
                score = self._fmt(row.get("total_score", 0))
                logger.info(f"  {i}. {name} ({code}) 评分:{score}")

        logger.info("\n[风险提示] 本报告为量化辅助研究工具，不构成投资建议。")

    def _render_rich(
        self,
        short_term: pd.DataFrame,
        mid_term: pd.DataFrame,
        target_date: date,
    ) -> None:
        """Rich 终端彩色表格渲染"""
        try:
            from rich.console import Console
            from rich.table import Table
            from rich.panel import Panel
            from rich.text import Text

            console = Console()

            # 标题
            title = Text(f"A股智能选股日报 — {target_date.isoformat()}", style="bold cyan")
            console.print(Panel(title, border_style="cyan"))
            console.print()

            # 短线表格
            if not short_term.empty:
                st_table = Table(title="⚡ 短线精选", title_style="bold yellow", border_style="yellow")
                st_table.add_column("#", style="dim", width=3)
                st_table.add_column("代码", style="cyan")
                st_table.add_column("名称", style="bold")
                st_table.add_column("总分", justify="right", style="green")
                st_table.add_column("涨跌幅", justify="right")
                st_table.add_column("入选理由", style="italic")

                for i, (idx, row) in enumerate(short_term.iterrows(), 1):
                    code = str(row.get("code", idx))
                    name = str(row.get("name", ""))
                    total = self._fmt(row.get("total_score", 0))
                    pct = row.get("pct_change", 0)
                    pct_str = f"[red]{pct:+.2f}%[/red]" if isinstance(pct, (int, float)) and pct > 0 else f"[green]{pct:+.2f}%[/green]" if isinstance(pct, (int, float)) else "-"
                    reason = self._build_reason(row)
                    st_table.add_row(str(i), code, name, total, pct_str, reason)

                console.print(st_table)
                console.print()

            else:
                console.print("[yellow]⚠️ 今日无短线推荐[/yellow]")
                console.print()

            # 中线表格
            if not mid_term.empty:
                mt_table = Table(title="🏔️ 中线趋势股", title_style="bold blue", border_style="blue")
                mt_table.add_column("#", style="dim", width=3)
                mt_table.add_column("代码", style="cyan")
                mt_table.add_column("名称", style="bold")
                mt_table.add_column("总分", justify="right", style="green")
                mt_table.add_column("趋势", justify="right")
                mt_table.add_column("基本面", justify="right")
                mt_table.add_column("核心理由", style="italic")

                for i, (idx, row) in enumerate(mid_term.iterrows(), 1):
                    code = str(row.get("code", idx))
                    name = str(row.get("name", ""))
                    total = self._fmt(row.get("total_score", 0))
                    trend = self._fmt(row.get("score_trend", 0))
                    fund = self._fmt(row.get("score_fundamentals", 0))
                    reason = self._build_mid_reason(row)
                    mt_table.add_row(str(i), code, name, total, trend, fund, reason)

                console.print(mt_table)
            else:
                console.print("[dim]💤 今日无中线推荐（无股票达标）[/dim]")

            console.print()
            console.print("[dim]⚠️ 风险提示: 本报告为量化辅助研究工具，不构成投资建议。投资有风险，入市需谨慎。[/dim]")

        except (ImportError, UnicodeEncodeError, UnicodeDecodeError, Exception):
            # Rich 未安装或Windows编码问题，降级为纯文本摘要
            logger.warning("Rich 终端渲染跳过（编码或依赖问题），使用纯文本输出")
            self._render_plain(short_term, mid_term, target_date)
