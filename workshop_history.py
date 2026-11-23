"""Durable workshop conversations and immutable versions of the editable prompts."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime
from difflib import unified_diff
from pathlib import Path
from uuid import uuid4

if __package__:
    from .paipan import BEIJING
    from .user_management import ManagementError
else:
    from paipan import BEIJING
    from user_management import ManagementError

MAX_TURNS = 8
PAGE_SIZE = 10
SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (actor TEXT PRIMARY KEY, revision TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY, actor TEXT NOT NULL, at TEXT NOT NULL, status TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1, provider TEXT NOT NULL, focus TEXT NOT NULL,
    message TEXT NOT NULL, material TEXT NOT NULL, reply TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS actor_turns ON turns(actor, id);
CREATE INDEX IF NOT EXISTS active_turns ON turns(actor, active, status, id);
CREATE TABLE IF NOT EXISTS changes (
    id TEXT PRIMARY KEY, turn_id INTEGER NOT NULL, section TEXT NOT NULL,
    base TEXT NOT NULL, base_version INTEGER NOT NULL, before TEXT NOT NULL, after TEXT NOT NULL,
    reason TEXT NOT NULL, status TEXT NOT NULL, decided_at TEXT, version INTEGER
);
CREATE INDEX IF NOT EXISTS turn_changes ON changes(turn_id);
CREATE INDEX IF NOT EXISTS section_changes ON changes(section, status, base_version);
CREATE TABLE IF NOT EXISTS versions (
    section TEXT NOT NULL, number INTEGER NOT NULL, text TEXT NOT NULL, at TEXT NOT NULL,
    source TEXT NOT NULL, actor TEXT, change_id TEXT UNIQUE, PRIMARY KEY(section, number)
);
CREATE TABLE IF NOT EXISTS reading_tests (
    id INTEGER PRIMARY KEY, actor TEXT NOT NULL, request_id TEXT NOT NULL, at TEXT NOT NULL,
    source TEXT NOT NULL, serial INTEGER, question TEXT NOT NULL, status TEXT NOT NULL,
    report TEXT NOT NULL, UNIQUE(actor, request_id)
);
"""


def _now():
    return datetime.now(BEIJING).isoformat(timespec="seconds")


def history_path():
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path

    return Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_liuyao" / "workshop.db"


def _revision(db, actor):
    row = db.execute("SELECT revision FROM sessions WHERE actor=?", (actor,)).fetchone()
    if row:
        return row["revision"]
    revision = uuid4().hex
    db.execute("INSERT INTO sessions VALUES (?, ?)", (actor, revision))
    return revision


def _advance(db, actor):
    db.execute("UPDATE sessions SET revision=? WHERE actor=?", (uuid4().hex, actor))


def _turn(db, row):
    turn = dict(row)
    turn.update(json.loads(turn.pop("material")))
    turn["changes"] = [
        dict(item)
        for item in db.execute(
            "SELECT * FROM changes WHERE turn_id=? ORDER BY rowid", (turn["id"],)
        )
    ]
    return turn


def _archive(db, ids):
    for turn_id in ids:
        db.execute("UPDATE turns SET active=0 WHERE id=?", (turn_id,))
        db.execute(
            "UPDATE changes SET status='archived', decided_at=? "
            "WHERE turn_id=? AND status='pending'",
            (_now(), turn_id),
        )


def _page(db, select, where, args, order, page):
    total = db.execute("SELECT COUNT(*) " + where, args).fetchone()[0]
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, pages)
    rows = db.execute(
        select + " " + where + " ORDER BY " + order + " DESC LIMIT ? OFFSET ?",
        (*args, PAGE_SIZE, (page - 1) * PAGE_SIZE),
    )
    return {"items": [dict(row) for row in rows], "total": total, "page": page, "pages": pages}


class WorkshopHistory:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(SCHEMA)
            # A previous instance may have exited while waiting for its model.
            db.execute(
                "UPDATE turns SET status='interrupted', active=0, reply='插件重载时本轮尚未完成。' "
                "WHERE status='running'"
            )
            db.execute("UPDATE reading_tests SET status='interrupted' WHERE status='running'")

    @contextmanager
    def connect(self):
        try:
            with closing(sqlite3.connect(self.path)) as db, db:
                db.row_factory = sqlite3.Row
                yield db
        except sqlite3.Error as exc:
            raise ManagementError("解卦管理记录读写失败，请检查插件数据目录后重试", 503) from exc

    def existing_test(self, actor, request_id):
        with self.connect() as db:
            row = db.execute(
                "SELECT id FROM reading_tests WHERE actor=? AND request_id=?", (actor, request_id)
            ).fetchone()
            return row["id"] if row else None

    def start_test(self, actor, request_id, source, serial, question, report):
        with self.connect() as db:
            row = db.execute(
                "INSERT INTO reading_tests (actor, request_id, at, source, serial, question, "
                "status, report) VALUES (?, ?, ?, ?, ?, ?, 'running', ?) "
                "ON CONFLICT(actor, request_id) DO NOTHING",
                (
                    actor,
                    request_id,
                    _now(),
                    source,
                    serial,
                    question,
                    json.dumps(report, ensure_ascii=False),
                ),
            )
            if row.rowcount:
                return row.lastrowid, True
            return db.execute(
                "SELECT id FROM reading_tests WHERE actor=? AND request_id=?", (actor, request_id)
            ).fetchone()["id"], False

    def save_test(self, test_id, status, report):
        with self.connect() as db:
            db.execute(
                "UPDATE reading_tests SET status=?, report=? WHERE id=? AND status='running'",
                (status, json.dumps(report, ensure_ascii=False, default=str), test_id),
            )

    def list_tests(self, page):
        with self.connect() as db:
            return _page(
                db,
                "SELECT id, actor, at, source, serial, question, status",
                "FROM reading_tests",
                (),
                "id",
                page,
            )

    def stop_unfinished_test(self, test_id, status):
        with self.connect() as db:
            db.execute(
                "UPDATE reading_tests SET status=? WHERE id=? AND status='running'",
                (status, test_id),
            )

    def test_detail(self, test_id):
        with self.connect() as db:
            row = db.execute(
                "SELECT id, actor, at, source, serial, question, status, report "
                "FROM reading_tests WHERE id=?",
                (test_id,),
            ).fetchone()
            if not row:
                raise ManagementError("找不到该解卦测试记录", 404)
            result = dict(row)
            return result | {"report": json.loads(result["report"])}

    def sync_versions(self, rules):
        """Finish interrupted approvals before recording other changes to the live config."""
        numbers = {}
        with self.connect() as db:
            for section, text in rules.items():
                latest = db.execute(
                    "SELECT number, text FROM versions WHERE section=? "
                    "ORDER BY number DESC LIMIT 1",
                    (section,),
                ).fetchone()
                number = latest["number"] if latest else 0
                applying = db.execute(
                    "SELECT c.*, t.actor FROM changes c JOIN turns t ON t.id=c.turn_id "
                    "WHERE c.section=? AND c.status='applying'",
                    (section,),
                ).fetchone()
                source = "config" if latest else "initial"
                actor = change_id = None
                at = _now()
                if applying:
                    expected = latest["text"].replace(applying["before"], applying["after"], 1)
                    if applying["base_version"] == number and expected.strip() == text:
                        source, actor, change_id = "workshop", applying["actor"], applying["id"]
                        at = applying["decided_at"]
                        db.execute(
                            "UPDATE changes SET status='approved', version=? WHERE id=?",
                            (number + 1, change_id),
                        )
                        _advance(db, actor)
                    else:
                        db.execute(
                            "UPDATE changes SET status='pending', decided_at=NULL WHERE id=?",
                            (applying["id"],),
                        )
                if not latest or latest["text"] != text:
                    number += 1
                    db.execute(
                        "INSERT INTO versions VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (section, number, text, at, source, actor, change_id),
                    )
                db.execute(
                    "UPDATE changes SET status='stale', decided_at=? "
                    "WHERE section=? AND status='pending' AND base_version<>?",
                    (_now(), section, number),
                )
                numbers[section] = number
        return numbers

    def conversation(self, actor):
        with self.connect() as db:
            revision = _revision(db, actor)
            turns = db.execute(
                "SELECT * FROM turns WHERE actor=? AND active=1 AND status='done' ORDER BY id",
                (actor,),
            )
            return {"revision": revision, "turns": [_turn(db, row) for row in turns]}

    def start(self, actor, *, provider, focus, message, **material):
        with self.connect() as db:
            return db.execute(
                "INSERT INTO turns (actor, at, status, provider, focus, message, material) "
                "VALUES (?, ?, 'running', ?, ?, ?, ?)",
                (actor, _now(), provider, focus, message, json.dumps(material, ensure_ascii=False)),
            ).lastrowid

    def finish(self, turn_id, actor, reply, changes, versions):
        with self.connect() as db:
            result = db.execute(
                "UPDATE turns SET status='done', reply=? WHERE id=? AND status='running'",
                (reply, turn_id),
            )
            if not result.rowcount:
                raise ManagementError("本轮已中断，请刷新后查看优化记录", 409)
            for change in changes:
                db.execute(
                    "INSERT INTO changes (id, turn_id, section, base, base_version, before, after, "
                    "reason, status) VALUES (:id, :turn_id, :section, :base, :base_version, "
                    ":before, :after, :reason, :status)",
                    change | {"turn_id": turn_id, "base_version": versions[change["section"]]},
                )
            older = db.execute(
                "SELECT id FROM turns WHERE actor=? AND active=1 AND status='done' "
                "ORDER BY id DESC LIMIT -1 OFFSET ?",
                (actor, MAX_TURNS),
            ).fetchall()
            _archive(db, [row["id"] for row in older])
            _advance(db, actor)

    def fail(self, turn_id, error, *, interrupted=False):
        with self.connect() as db:
            db.execute(
                "UPDATE turns SET status=?, reply=?, active=0 WHERE id=? AND status='running'",
                ("interrupted" if interrupted else "failed", error, turn_id),
            )

    def decide(self, change_id, actor, status):
        with self.connect() as db:
            changed = db.execute(
                "UPDATE changes SET status=?, decided_at=? WHERE id=? AND status='pending' AND "
                "turn_id IN (SELECT id FROM turns WHERE actor=? AND active=1)",
                (status, _now(), change_id, actor),
            )
            if not changed.rowcount:
                raise ManagementError("建议状态已变化，请刷新后核对", 409)
            if status == "dismissed":
                _advance(db, actor)

    def reset(self, actor):
        with self.connect() as db:
            ids = db.execute("SELECT id FROM turns WHERE actor=? AND active=1", (actor,)).fetchall()
            _archive(db, [row["id"] for row in ids])
            _advance(db, actor)

    def history(self, actor, page):
        with self.connect() as db:
            return _page(
                db,
                "SELECT id, at, status, provider, focus, substr(message,1,100) AS message, "
                "(SELECT COUNT(*) FROM changes WHERE turn_id=turns.id) AS proposals, "
                "(SELECT COUNT(*) FROM changes WHERE turn_id=turns.id AND status='approved') "
                "AS approved",
                "FROM turns WHERE actor=?",
                (actor,),
                "id",
                page,
            )

    def history_detail(self, actor, turn_id):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM turns WHERE actor=? AND id=?", (actor, turn_id)
            ).fetchone()
            if not row:
                raise ManagementError("找不到本账号的优化记录", 404)
            return _turn(db, row)

    def versions(self, section, page):
        with self.connect() as db:
            return _page(
                db,
                "SELECT number, at, source, actor",
                "FROM versions WHERE section=?",
                (section,),
                "number",
                page,
            )

    def version_detail(self, section, number):
        with self.connect() as db:
            row = db.execute(
                "SELECT v.*, c.reason, c.before, c.after, c.turn_id, p.text AS previous "
                "FROM versions v LEFT JOIN changes c ON c.id=v.change_id "
                "LEFT JOIN versions p ON p.section=v.section AND p.number=v.number-1 "
                "WHERE v.section=? AND v.number=?",
                (section, number),
            ).fetchone()
            if not row:
                raise ManagementError("找不到该提示词版本", 404)
            detail = dict(row)
            if detail["source"] == "config":
                detail["diff"] = "\n".join(
                    unified_diff(
                        detail["previous"].splitlines(),
                        detail["text"].splitlines(),
                        fromfile=f"V{detail['number'] - 1}",
                        tofile=f"V{detail['number']}",
                        lineterm="",
                    )
                )
            return detail
