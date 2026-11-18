"""全局配置与业务常量。

所有“魔法数字”集中在这里，PRD / 测试 / 文档都以本文件为唯一来源。
环境变量 CROSSMATE_AI_MODE: mock(默认) | real  —— real 需要另配 AI_GATEWAY_* 。
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]  # backend/
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

API_PREFIX = "/api/v0.2"
DB_PATH = os.getenv("CROSSMATE_DB", str(DATA_DIR / "crossmate.db"))

# ---------- AI ----------
AI_MODE = os.getenv("CROSSMATE_AI_MODE", "mock")  # mock | real
AI_BASE_URL = os.getenv("AI_GATEWAY_BASE_URL", "")
AI_API_KEY = os.getenv("AI_GATEWAY_API_KEY", "")
AI_MODEL = os.getenv("AI_GATEWAY_MODEL", "claude-sonnet-5-5")
AI_TIMEOUT_SECONDS = float(os.getenv("AI_TIMEOUT_SECONDS", "2.0"))
AI_REPAIR_ONCE = True  # Schema 校验不过时允许一次结构修复，仍失败则 fallback

# ---------- 认证 ----------
CODE_TTL_SECONDS = 300          # 验证码 5 分钟有效
CODE_RESEND_SECONDS = 60        # 60 秒内不可重发
CODE_MAX_ATTEMPTS = 5           # 错 5 次
CODE_LOCK_SECONDS = 900         # 锁 15 分钟
SESSION_TTL_SECONDS = 7 * 86400
MIN_AGE = 18

# ---------- 资料 ----------
MIN_INTERESTS = 3
NICKNAME_MAX = 24
BIO_MAX = 300
INTERESTS_MAX = 12
VISIBILITY_CHOICES = {"public", "matches_only", "private"}
SOCIAL_GOALS = {"make_friends", "language_exchange", "good_conversations", "dating"}

# ---------- Discover / 推荐 ----------
RECOMMEND_WEIGHTS = {
    "interest": 0.30,
    "language_complement": 0.20,
    "timezone_overlap": 0.20,
    "social_goal": 0.15,
    "activity": 0.15,
}  # 合计 1.0
RISK_PENALTY_MAX = 0.30         # 风险为独立扣分项
PASS_COOLDOWN_HOURS = 24        # 跳过后的冷却
PRIORITY_LIKE_DAILY_QUOTA = 3   # 每日重点喜欢额度（不可用积分购买）
DISCOVER_PAGE_SIZE = 10

# ---------- 聊天 ----------
TEXT_MIN = 1
TEXT_MAX = 2000

# ---------- 房间 ----------
ROOM_TABS = {"popular", "language", "topics"}
ROOM_MESSAGE_MAX = 500

# ---------- 通话 ----------
CALL_RING_TIMEOUT_SECONDS = 45

# ---------- 积分 ----------
WEEKLY_POINTS_CAP = 100
REWARD_RULES = {
    # code: (points, 说明, 终身次数上限(None=不限), 观察期小时)
    "PROFILE_COMPLETE": (20, "完成必填资料且兴趣至少 3 个", 1, 0),
    "FIRST_MATCH": (10, "首次真实双向喜欢", 1, 24),
    "FIRST_VALID_INTERACTION": (30, "24 小时内双方各至少 2 条成功送达消息且未拉黑/违规", 1, 24),
    "QUALITY_FEEDBACK": (3, "合格提交通话或房间反馈", None, 0),  # UTC 周最多 2 次
}
QUALITY_FEEDBACK_WEEKLY_LIMIT = 2
REDEMPTION_ITEMS = {
    "theme_sunset": {"name": "Sunset theme", "cost": 20, "type": "theme"},
    "frame_wave": {"name": "Wave frame", "cost": 40, "type": "avatar_frame"},
}


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


# ---------- 演示 / 测试用的时间加速 ----------
# valid interaction 需要“24 小时内”，正常流程里要等；demo 可用加速倍率。
TIME_SCALE = max(1, _int_env("CROSSMATE_TIME_SCALE", 1))
# 演示环境打开后可让所有奖励观察期立即结算（仅 testing/demo）
INSTANT_REWARD_SETTLEMENT = os.getenv("CROSSMATE_INSTANT_REWARDS", "0") == "1"