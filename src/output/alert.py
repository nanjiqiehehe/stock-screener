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
    生成微信推送专用摘要（简洁、适配手机屏幕）

    返回纯文本，长度 < 4096 字符（微信限制）
    """
    import pandas as pd

    lines = []
    lines.append(f"📊 A股选股日报 {target_date.isoformat()}")

    # 市场概况
    if market_summary:
        lines.append("")
        for k, v in market_summary.items():
            lines.append(f"  {k}: {v}")

    # 短线推荐
    if hasattr(short_df, 'empty') and not short_df.empty:
        lines.append(f"\n⚡ 短线精选 Top{len(short_df)}:")
        for i, (idx, row) in enumerate(short_df.iterrows(), 1):
            code = row.get("code", idx)
            name = row.get("name", "")
            score = row.get("total_score", 0)
            pct = row.get("pct_change", 0)
            pct_s = f"{pct:+.1f}%" if isinstance(pct, (int, float)) else ""
            lines.append(f"  {i}.{name}({code}) 评分{score:.0f} {pct_s}")
    else:
        lines.append("\n⚡ 短线: 今日无推荐")

    # 中线推荐
    if hasattr(mid_df, 'empty') and not mid_df.empty:
        lines.append(f"\n🏔️ 中线趋势 Top{len(mid_df)}:")
        for i, (idx, row) in enumerate(mid_df.iterrows(), 1):
            code = row.get("code", idx)
            name = row.get("name", "")
            score = row.get("total_score", 0)
            lines.append(f"  {i}.{name}({code}) 评分{score:.0f}")
    else:
        lines.append("\n🏔️ 中线: 无股票达标")

    lines.append(f"\n⏰ {pd.Timestamp.now().strftime('%H:%M')} | 量化辅助研究，不构成投资建议")
    return "\n".join(lines)
