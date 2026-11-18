"""演示种子数据：8 个可一键登录的账号 + 3 个语音房。

用法：python -m app.seed [--reset]
账号密码：无需密码，验证码直接返回（demo 模式）。
"""
from __future__ import annotations

import sys

from app.core import db, util

DEMO_USERS = [
    # email 前缀, nickname, country, timezone, languages, goals, interests, bio
    ("alex", "Alex", "United States", "UTC-5", ["English", "中文"], ["make_friends", "language_exchange"],
     ["Indie", "Travel", "Coffee", "Design"], "Learning languages, collecting small moments."),
    ("sam", "Sam", "Singapore", "UTC+8", ["English", "中文"], ["language_exchange", "good_conversations"],
     ["Indie", "Food", "Gaming", "Music"], "Jazz on repeat. Ask me about night markets."),
    ("maya", "Maya", "United Kingdom", "UTC+0", ["English", "Español"], ["make_friends", "dating"],
     ["Travel", "Music", "Photography"], "Weekend walks and vinyl records."),
    ("kenji", "Kenji", "Japan", "UTC+9", ["日本語", "English"], ["good_conversations"],
     ["Coffee", "Films", "Design"], "Film photography and pour-over coffee."),
    ("lina", "Lina", "Brazil", "UTC-3", ["Português", "English"], ["make_friends", "language_exchange"],
     ["Music", "Football", "Travel"], "Samba, surf and new friends."),
    ("omar", "Omar", "United Arab Emirates", "UTC+4", ["العربية", "English"], ["good_conversations"],
     ["Coffee", "Books", "Travel"], "Books over breakfast."),
    ("nadia", "Nadia", "Indonesia", "UTC+7", ["Bahasa", "English"], ["make_friends"],
     ["Music", "Food", "Gaming"], "Foodie who loves late-night talks."),
    ("leo", "Leo", "Germany", "UTC+1", ["Deutsch", "English"], ["language_exchange"],
     ["Design", "Indie", "Coffee"], "Berlin based, learning Chinese."),
]

DEMO_ROOMS = [
    ("Late night language lounge", "Talk, listen, and meet people from everywhere. Languages and music.",
     "en", "alex"),
    ("English practice · beginners", "Slow-paced English conversation for beginners.", "en", "sam"),
    ("Music after midnight", "Share what you're listening to tonight.", "en", "maya"),
]


def seed(reset: bool = False) -> dict:
    if reset:
        with db.tx() as c:
            for t in ("users", "auth_codes", "sessions", "privacy", "blocks", "likes", "passes", "matches",
                      "messages", "rooms", "room_members", "room_messages", "calls", "rtc_events",
                      "notifications", "rewards_ledger", "redemptions", "feedback", "reports",
                      "ai_generations", "ai_adoptions", "ai_usage", "events", "audit_log"):
                c.execute(f"DELETE FROM {t}")
    ids = {}
    now = util.now()
    for i, (email, nick, country, tz, langs, goals, interests, bio) in enumerate(DEMO_USERS):
        full = f"{email}@demo.crossmate.app"
        row = db.q1("SELECT id FROM users WHERE email=?", (full,))
        if row:
            ids[email] = row["id"]
            continue
        uid = util.new_id("u")
        with db.tx() as c:
            c.execute(
                """INSERT INTO users(id,email,nickname,age_confirmed,status,country,timezone,languages,social_goals,
                   bio,interests,avatar_seed,profile_completed,consent_version,created_at,last_active_at)
                   VALUES(?,?,?,1,'active',?,?,?,?,?,?,?,1,'2026-11-01',?,?)""",
                (uid, full, nick, country, tz, util.dumps(langs), util.dumps(goals), bio, util.dumps(interests),
                 nick[0].lower(), now - i * 3600, now - (i * 60)),
            )
            c.execute("INSERT INTO privacy(user_id) VALUES(?)", (uid,))
        ids[email] = uid
    if not db.q1("SELECT 1 FROM rooms"):
        for title, topic, lang, host in DEMO_ROOMS:
            rid = util.new_id("r")
            with db.tx() as c:
                c.execute("INSERT INTO rooms(id,title,topic,language,host_id,status,created_at) VALUES(?,?,?,?,?, 'live', ?)",
                          (rid, title, topic, lang, ids[host], now))
                c.execute("INSERT INTO room_members(room_id,user_id,role,joined_at) VALUES(?,?, 'speaker', ?)",
                          (rid, ids[host], now))
    return ids


if __name__ == "__main__":
    result = seed(reset="--reset" in sys.argv)
    print("seeded users:")
    for k, v in result.items():
        print(f"  {k}@demo.crossmate.app  ({v})")