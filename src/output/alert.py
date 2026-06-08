"""
通知推送模块

支持：Server酱(微信)、企业微信、钉钉、飞书
"""
import json
import logging

import requests

logger = logging.getLogger(__name__)


class AlertSender:
    """消息推送"""

    def __init__(self, config: dict):
        self.cfg = config.get("notification", {})
        self.enabled = self.cfg.get("enabled", False)
        self.ntype = self.cfg.get("type", "serverchan")
        self.webhook = self.cfg.get("webhook_url", "")
        # Server酱 SendKey
        self.sendkey = self.cfg.get("sendkey", "")

    def send_report_summary(self, short_summary: str) -> bool:
        """推送报告摘要（~500字符，适配微信限制）"""
        if not self.enabled:
            return False

        if self.ntype == "serverchan":
            return self._send_serverchan(short_summary)
        elif self.ntype == "wechat_work":
            return self._send_wechat_work(short_summary)
        elif self.ntype == "dingtalk":
            return self._send_dingtalk(short_summary)
        elif self.ntype == "feishu":
            return self._send_feishu(short_summary)
        else:
            logger.warning(f"不支持的通知类型: {self.ntype}")
            return False

    # ========== Server酱 (微信推送) ==========

    def _send_serverchan(self, text: str) -> bool:
        """
        Server酱 v2 API: https://sct.ftqq.com/
        免费版每天最多5条推送，通过微信服务号接收。
        拿到 SendKey 后填入 config.yaml 即可。
        """
        key = self.sendkey or self.webhook
        if not key:
            logger.warning("Server酱 SendKey 未配置")
            return False

        url = f"https://sctapi.ftqq.com/{key}.send"
        payload = {
            "title": "A股选股日报",
            "desp": text,
        }
        try:
            resp = requests.post(url, data=payload, timeout=15)
            result = resp.json()
            if result.get("code") == 0:
                logger.info("Server酱推送成功")
                return True
            else:
                logger.warning(f"Server酱推送失败: {resp.text}")
                return False
        except Exception as e:
            logger.error(f"Server酱推送异常: {e}")
            return False

    # ========== 企业微信 ==========

    def _send_wechat_work(self, text: str) -> bool:
        """企业微信机器人 Webhook"""
        if not self.webhook:
            return False
        payload = {
            "msgtype": "markdown",
            "markdown": {"content": text[:4096]},
        }
        try:
            resp = requests.post(self.webhook, json=payload, timeout=10)
            if resp.status_code == 200 and resp.json().get("errcode") == 0:
                logger.info("企业微信推送成功")
                return True
            else:
                logger.warning(f"企业微信推送失败: {resp.text}")
                return False
        except Exception as e:
            logger.error(f"企业微信推送异常: {e}")
            return False

    # ========== 钉钉 ==========

    def _send_dingtalk(self, text: str) -> bool:
        """钉钉机器人"""
        if not self.webhook:
            return False
        payload = {
            "msgtype": "markdown",
            "markdown": {"title": "A股选股日报", "text": text[:20000]},
        }
        try:
            resp = requests.post(self.webhook, json=payload, timeout=10)
            if resp.status_code == 200 and resp.json().get("errcode") == 0:
                logger.info("钉钉推送成功")
                return True
            else:
                logger.warning(f"钉钉推送失败: {resp.text}")
                return False
        except Exception as e:
            logger.error(f"钉钉推送异常: {e}")
            return False

    # ========== 飞书 ==========

    def _send_feishu(self, text: str) -> bool:
        """飞书机器人"""
        if not self.webhook:
            return False
        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": "A股选股日报"},
                    "template": "blue",
                },
                "elements": [{"tag": "markdown", "content": text[:30000]}],
            },
        }
        try:
            resp = requests.post(self.webhook, json=payload, timeout=10)
            if resp.status_code == 200 and resp.json().get("code") == 0:
                logger.info("飞书推送成功")
                return True
            else:
                logger.warning(f"飞书推送失败: {resp.text}")
                return False
        except Exception as e:
            logger.error(f"飞书推送异常: {e}")
            return False


def build_push_summary(short_df, mid_df, target_date, market_summary=None) -> str:
    """
    生成微信推送专用摘要（精心排版，适配手机屏幕）

    用 emoji + 分隔线 + 等宽对齐，在纯文本约束下做到清晰美观。
    """
    import pandas as pd
    from datetime import date

    # 市场状态判断
    is_today = (target_date == date.today())
    date_label = f"📅 {target_date.isoformat()}  {['周一','周二','周三','周四','周五','周六','周日'][target_date.weekday()]}"

    lines = []
    lines.append(f"╔══════════════════════╗")
    lines.append(f"║   A股智能选股日报     ║")
    lines.append(f"╠══════════════════════╣")
    lines.append(f"║ {date_label}     ║")
    lines.append(f"╚══════════════════════╝")

    # 市场概况
    if market_summary:
        lines.append("")
        lines.append("📊 大盘概况")
        lines.append("─────────────────")
        for k, v in market_summary.items():
            lines.append(f"  {k}：{v}")

    # 短线推荐
    lines.append("")
    if hasattr(short_df, 'empty') and not short_df.empty:
        n = len(short_df)
        lines.append(f"⚡ 短线精选 TOP{n}")
        lines.append("─────────────────")
        for i, (idx, row) in enumerate(short_df.iterrows(), 1):
            code = str(row.get("code", idx))
            name = str(row.get("name", ""))[:6]
            score = row.get("total_score", 0)
            pct = row.get("pct_change", 0)
            pct_s = f"{pct:+.1f}%" if isinstance(pct, (int, float)) else ""

            # 评分等级 emoji
            if score >= 50:
                star = "🔥"
            elif score >= 40:
                star = "⭐"
            elif score >= 30:
                star = "✨"
            else:
                star = "  "

            # 连板标签
            cons = row.get("consecutive", 0)
            tag = f" {int(cons)}连板" if cons and cons >= 2 else ""
            if isinstance(pct, (int, float)) and pct >= 20:
                tag += " 20cm"

            line = f" {star} {i}.{name[:4]:　<4} {code}  {score:.0f}分  {pct_s}{tag}"
            lines.append(line)
    else:
        lines.append("⚡ 短线精选：今日无推荐")

    # 中线推荐
    lines.append("")
    if hasattr(mid_df, 'empty') and not mid_df.empty:
        n = len(mid_df)
        lines.append(f"🏔️ 中线趋势 TOP{n}")
        lines.append("─────────────────")
        for i, (idx, row) in enumerate(mid_df.iterrows(), 1):
            code = str(row.get("code", idx))
            name = str(row.get("name", ""))[:6]
            score = row.get("total_score", 0)
            tag = ""
            if row.get("score_trend", 0) >= 65:
                tag += " 趋势↑"
            if row.get("score_fundamentals", 0) >= 60:
                tag += " 基本面优"
            lines.append(f"  {i}.{name[:4]:　<4} {code}  {score:.0f}分{tag}")
    else:
        lines.append("🏔️ 中线趋势：无股票达标")

    # 完整报告链接
    lines.append("")
    lines.append("─────────────────")
    lines.append("📋 完整图表看板 →")
    lines.append("https://nanjiqiehehe.github.io/stock-screener/")

    # 风险提示
    lines.append("")
    lines.append("⚠️ 量化辅助研究 · 非投资建议")
    lines.append(f"🤖 {pd.Timestamp.now().strftime('%m/%d %H:%M')} 自动生成")

    msg = "\n".join(lines)
    # 控制长度
    if len(msg) > 3500:
        msg = msg[:3490] + "\n\n... (内容过长已截断)"
    return msg
