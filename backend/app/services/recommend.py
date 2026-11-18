"""推荐算法（FR04 + 商业化修复建议）。

最终得分：
    score = Σ w_i * feature_i                       （正向，权重合计 1.0）
            - risk_penalty                          （独立，0~0.30）
            + freshness/serendipity 抖动            （多样性与曝光公平）
合规约束（硬编码，不可配置）：
    - 敏感属性（性别/性取向/宗教/种族）不参与排序；
    - 积分（points）不参与排序；
    - 房间停留时长不参与排序；
    - 与商业化耦合的 “popularity/pay” 因子默认关闭（RESPONSIBLE_MODE=True）。
"""
from __future__ import annotations

import hashlib
import random

from app.core import db, util
from app.core.config import PASS_COOLDOWN_HOURS, RECOMMEND_WEIGHTS, RISK_PENALTY_MAX
from app.routers.users import blocked_between, public_view

RESPONSIBLE_MODE = True  # 关闭付费/人气加权，避免“花钱买曝光”


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _tz_overlap(a: str | None, b: str | None) -> float:
    """按时区 UTC 偏移比较；差 0~2h 视为高重合，>8h 为 0。"""
    def off(tz: str | None) -> int | None:
        if not tz:
            return None
        try:
            if tz.startswith("UTC") or tz.startswith("GMT"):
                sign = -1 if "-" in tz else 1
                num = tz.replace("UTC", "").replace("GMT", "").replace("+", "").replace("-", "") or "0"
                return sign * int(float(num.split(":")[0]))
            from zoneinfo import ZoneInfo
            import datetime

            d = datetime.datetime.now(ZoneInfo(tz))
            return int(d.utcoffset().total_seconds() // 3600)
        except Exception:  # noqa: BLE001
            return None

    x, y = off(a), off(b)
    if x is None or y is None:
        return 0.0
    gap = abs(x - y)
    return max(0.0, 1 - gap / 8)


def _activity(c: dict, now: float) -> float:
    """活跃度来自 users.last_active_at，7 天内线性衰减到 0。"""
    hours = (now - (c.get("last_active_at") or 0)) / 3600
    return max(0.0, 1 - hours / 168)


def _risk_penalty(candidate: dict, now: float) -> float:
    p = 0.0
    # 被多次举报：每单高风险 -0.10，中风险 -0.05，上限 0.30
    rows = db.q("SELECT risk_level FROM reports WHERE target_user_id=? AND created_at>=?",
                (candidate["id"], now - 30 * 86400))
    for r in rows:
        p += 0.10 if r["risk_level"] == "high" else 0.05 if r["risk_level"] == "medium" else 0
    # 资料极不完整：轻微降权
    if not candidate.get("interests"):
        p += 0.05
    return min(p, RISK_PENALTY_MAX)


def rank(viewer: dict, candidates: list[dict], now: float | None = None) -> list[dict]:
    now = now or util.now()
    vi = set(viewer.get("interests") or [])
    vl = set(viewer.get("languages") or [])
    vg = set(viewer.get("social_goals") or [])
    out = []
    _exposure = {r["cid"]: r["n"] for r in db.q(
        "SELECT json_extract(props,'$.candidate_id') AS cid, COUNT(*) AS n "
        "FROM events WHERE name='discover_exposed' AND created_at>=? GROUP BY cid",
        (now - 7 * 86400,))}
    for c in candidates:
        ci = set(util.loads(c["interests"], []))
        cl = set(util.loads(c["languages"], []))
        cg = set(util.loads(c["social_goals"], []))
        feats = {
            "interest": _jaccard(vi, ci),
            "language_complement": _jaccard(vl, cl),
            "timezone_overlap": _tz_overlap(viewer.get("timezone"), c.get("timezone")),
            "social_goal": _jaccard(vg, cg),
            "activity": _activity(c, now),
        }
        base = sum(RECOMMEND_WEIGHTS[k] * v for k, v in feats.items())
        risk = _risk_penalty(c, now)
        score = base - risk
        if RESPONSIBLE_MODE:
            # 曝光公平：对历史曝光过多的候选轻微降权（防止赢家通吃）
            exp = _exposure.get(c["id"], 0)
            score -= min(0.05, exp / 2000)
        # 随机抖动 ±0.02，提升探索/多样性（同一用户同一天稳定）
        seed = int(hashlib.md5(f"{viewer['id']}:{c['id']}:{int(now // 86400)}".encode()).hexdigest()[:8], 16)
        jitter = (seed % 4001) / 100000 - 0.02
        out.append({**c, "_features": feats, "_base": round(base, 4), "_risk": round(risk, 4),
                    "_score": round(score + jitter, 4)})
    out.sort(key=lambda x: x["_score"], reverse=True)
    return out


def discover(viewer: dict, tab: str, limit: int, offset: int = 0) -> list[dict]:
    now = util.now()
    uid = viewer["id"]
    passed = {r["to_id"] for r in db.q(
        "SELECT to_id FROM passes WHERE from_id=? AND created_at>?", (uid, now - PASS_COOLDOWN_HOURS * 3600))}
    matched = {r["user_b"] if r["user_a"] == uid else r["user_a"]
               for r in db.q("SELECT user_a,user_b FROM matches WHERE user_a=? OR user_b=?", (uid, uid))}
    rows = db.q("SELECT * FROM users WHERE id<>? AND status='active' AND profile_completed=1", (uid,))
    cands = []
    for r in rows:
        if r["id"] in passed or r["id"] in matched or blocked_between(uid, r["id"]):
            continue
        if tab == "language" and not (set(util.loads(r["languages"], [])) - set(viewer.get("languages") or [])):
            continue
        if tab == "online" and now - (r["last_active_at"] or 0) > 900:  # 15 分钟内活跃
            continue
        cands.append(dict(r))
    ranked = rank(viewer, cands, now)[offset: offset + limit]
    result = []
    for c in ranked:
        if tab != "online" and now - (c["last_active_at"] or 0) > 900:
            pass  # 未在线也可以出现，只是不加权
        p = db.q1("SELECT * FROM privacy WHERE user_id=?", (c["id"],))
        item = public_view(c, uid, dict(p) if p else {})
        # ⚠️ 合规：这里故意不返回 points / 性别 / 国籍之外的敏感属性
        item["match_hint"] = _hint(c, uid)  # 只展示可解释依据，不展示伪精确百分比
        item["online"] = now - (c["last_active_at"] or 0) < 900
        result.append(item)
        util.track(uid, "discover_exposed", candidate_id=c["id"], tab=tab, score=c["_score"])
    return result


def _hint(c: dict, viewer_id: str) -> str:
    """可解释的匹配理由 —— 用“共同点”而不是“92% 匹配”。"""
    v = db.q1("SELECT interests FROM users WHERE id=?", (viewer_id,))
    shared = set(util.loads(v["interests"], [])) & set(util.loads(c["interests"], []))
    if shared:
        return "Shared: " + ", ".join(sorted(shared)[:3])
    return "New perspective"


def _unused_random() -> None:
    _ = random  # 保留以便未来做加权抽样