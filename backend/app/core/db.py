"""SQLite 存储层。所有访问走单个连接 + 线程锁（演示级，生产请换 PostgreSQL）。

设计要点：
- 表结构即数据模型，含义与 API 文档一一对应；
- 所有写操作在一个事务内完成（WAL 模式）；
- 奖励流水幂等键 (user_id, reward_code, source_id) 唯一约束兜底并发。
"""
from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager

from app.core.config import DB_PATH

_conn: sqlite3.Connection | None = None
_lock = threading.RLock()


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users(
  id TEXT PRIMARY KEY,
  email TEXT UNIQUE NOT NULL,
  nickname TEXT NOT NULL DEFAULT '',
  age_confirmed INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active',           -- active/frozen/deleting/deleted
  country TEXT, timezone TEXT, languages TEXT NOT NULL DEFAULT '[]',
  social_goals TEXT NOT NULL DEFAULT '[]', bio TEXT NOT NULL DEFAULT '',
  interests TEXT NOT NULL DEFAULT '[]', avatar_seed TEXT NOT NULL DEFAULT '',
  profile_completed INTEGER NOT NULL DEFAULT 0,
  consent_version TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL, last_active_at REAL NOT NULL, deleted_at REAL
);
CREATE TABLE IF NOT EXISTS auth_codes(
  email TEXT PRIMARY KEY, code TEXT NOT NULL, expires_at REAL NOT NULL,
  last_sent_at REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, locked_until REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions(
  token TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at REAL NOT NULL,
  expires_at REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS privacy(
  user_id TEXT PRIMARY KEY,
  country_visibility TEXT NOT NULL DEFAULT 'public',
  interests_visibility TEXT NOT NULL DEFAULT 'public',
  timezone_visibility TEXT NOT NULL DEFAULT 'matches_only'
);
CREATE TABLE IF NOT EXISTS blocks(
  blocker_id TEXT NOT NULL, blocked_id TEXT NOT NULL, created_at REAL NOT NULL,
  PRIMARY KEY(blocker_id, blocked_id)
);
CREATE TABLE IF NOT EXISTS likes(
  from_id TEXT NOT NULL, to_id TEXT NOT NULL, type TEXT NOT NULL,
  created_at REAL NOT NULL, PRIMARY KEY(from_id, to_id)
);
CREATE TABLE IF NOT EXISTS passes(
  from_id TEXT NOT NULL, to_id TEXT NOT NULL, created_at REAL NOT NULL,
  PRIMARY KEY(from_id, to_id)
);
CREATE TABLE IF NOT EXISTS matches(
  id TEXT PRIMARY KEY, user_a TEXT NOT NULL, user_b TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',        -- active/blocked/closed
  created_at REAL NOT NULL, closed_at REAL, close_reason TEXT,
  UNIQUE(user_a, user_b)
);
CREATE TABLE IF NOT EXISTS messages(
  id TEXT PRIMARY KEY, match_id TEXT NOT NULL, sender_id TEXT NOT NULL,
  client_message_id TEXT NOT NULL, text TEXT NOT NULL, source_generation_id TEXT,
  status TEXT NOT NULL DEFAULT 'delivered', created_at REAL NOT NULL,
  UNIQUE(match_id, client_message_id)
);
CREATE TABLE IF NOT EXISTS rooms(
  id TEXT PRIMARY KEY, title TEXT NOT NULL, topic TEXT NOT NULL, language TEXT NOT NULL,
  host_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'live',   -- scheduled/live/ended/frozen
  created_at REAL NOT NULL, ended_at REAL
);
CREATE TABLE IF NOT EXISTS room_members(
  room_id TEXT NOT NULL, user_id TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'listener',
  hand_raised INTEGER NOT NULL DEFAULT 0, muted_until REAL NOT NULL DEFAULT 0,
  joined_at REAL NOT NULL, left_at REAL,
  PRIMARY KEY(room_id, user_id)
);
CREATE TABLE IF NOT EXISTS room_messages(
  id TEXT PRIMARY KEY, room_id TEXT NOT NULL, user_id TEXT NOT NULL, text TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS calls(
  id TEXT PRIMARY KEY, caller_id TEXT NOT NULL, callee_id TEXT NOT NULL,
  match_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'requested',
  created_at REAL NOT NULL, ringing_at REAL, connected_at REAL, ended_at REAL,
  end_reason TEXT, a_ready INTEGER NOT NULL DEFAULT 0, b_ready INTEGER NOT NULL DEFAULT 0,
  feedback_submitted TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS rtc_events(
  provider_event_id TEXT PRIMARY KEY, call_id TEXT NOT NULL, event_type TEXT NOT NULL,
  payload TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS notifications(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, type TEXT NOT NULL, title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '', ref_id TEXT, read INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS rewards_ledger(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, reward_code TEXT NOT NULL,
  source_id TEXT NOT NULL, points INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
  reason TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, settled_at REAL,
  UNIQUE(user_id, reward_code, source_id)
);
CREATE TABLE IF NOT EXISTS redemptions(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, item_id TEXT NOT NULL, cost INTEGER NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, target_type TEXT NOT NULL,
  target_id TEXT NOT NULL, rating INTEGER NOT NULL, comment TEXT NOT NULL DEFAULT '',
  quality TEXT NOT NULL DEFAULT 'qualified', created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS reports(
  id TEXT PRIMARY KEY, reporter_id TEXT NOT NULL, target_user_id TEXT NOT NULL,
  target_type TEXT NOT NULL, target_id TEXT, category TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
  also_block INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'submitted',
  risk_level TEXT NOT NULL DEFAULT 'pending', queue TEXT NOT NULL DEFAULT 'pending',
  evidence_id TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_generations(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT NOT NULL, match_id TEXT,
  input TEXT NOT NULL, output TEXT NOT NULL, status TEXT NOT NULL,   -- succeeded/fallback/safety_blocked/failed
  fallback_reason TEXT NOT NULL DEFAULT '', latency_ms INTEGER NOT NULL DEFAULT 0,
  model TEXT NOT NULL DEFAULT 'mock', prompt_version TEXT NOT NULL DEFAULT '',
  retrieved TEXT NOT NULL DEFAULT '[]', created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_adoptions(
  id TEXT PRIMARY KEY, generation_id TEXT NOT NULL, user_id TEXT NOT NULL,
  suggestion_index INTEGER NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_usage(
  user_id TEXT NOT NULL, kind TEXT NOT NULL, day TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(user_id, kind, day)
);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, name TEXT NOT NULL,
  props TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT, action TEXT NOT NULL,
  target TEXT, meta TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msg_match ON messages(match_id, created_at);
CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications(user_id, created_at);
"""


def now() -> float:
    return time.time()


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        import app.core.config as cfg

        _conn = sqlite3.connect(cfg.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(SCHEMA)
    return _conn


@contextmanager
def tx():
    with _lock:
        c = get_conn()
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise


def q(sql: str, args: tuple | list = ()) -> list[sqlite3.Row]:
    with _lock:
        return get_conn().execute(sql, args).fetchall()


def q1(sql: str, args: tuple | list = ()) -> sqlite3.Row | None:
    rows = q(sql, args)
    return rows[0] if rows else None


def reset_for_tests(db_file: str) -> None:
    """测试用：切换 DB 文件并清空连接。"""
    global _conn
    import app.core.config as cfg

    if _conn is not None:
        _conn.close()
    _conn = None
    cfg.DB_PATH = db_file