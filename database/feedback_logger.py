"""
backend_fastapi/database/feedback_logger.py
사서의 필드 수정 내역을 SQLite에 저장한다.

테이블: feedback
  id               INTEGER PK AUTOINCREMENT
  isbn             TEXT     대상 ISBN
  field_tag        TEXT     수정된 MARC 필드 태그 (예: "300", "056")
  ai_value         TEXT     AI가 생성한 원본 값
  corrected_value  TEXT     사서가 수정한 최종 값
  librarian_note   TEXT     선택 메모
  created_at       TEXT     기록 시각 (ISO 8601)
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

# DB 파일 경로 — 환경변수로 오버라이드 가능
DB_PATH = os.getenv("FEEDBACK_DB_PATH", "./feedback.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """앱 시작 시 한 번 호출 — 테이블이 없으면 생성한다."""
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                isbn             TEXT    NOT NULL,
                field_tag        TEXT    NOT NULL,
                ai_value         TEXT    NOT NULL DEFAULT '',
                corrected_value  TEXT    NOT NULL DEFAULT '',
                librarian_note   TEXT    NOT NULL DEFAULT '',
                created_at       TEXT    NOT NULL
            )
        """)
        conn.commit()


def save_feedback_record(
    isbn: str,
    field_tag: str,
    ai_value: str,
    corrected_value: str,
    librarian_note: str = "",
) -> int:
    """
    피드백 한 건을 DB에 저장하고 생성된 id를 반환한다.

    Args:
        isbn:            대상 ISBN-13
        field_tag:       수정된 MARC 태그 (예: "300")
        ai_value:        AI 생성 원본 값
        corrected_value: 사서 최종 수정값
        librarian_note:  선택 메모

    Returns:
        새로 삽입된 행의 id (int)
    """
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO feedback
                (isbn, field_tag, ai_value, corrected_value, librarian_note, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (isbn, field_tag, ai_value, corrected_value, librarian_note, now),
        )
        conn.commit()
        return cur.lastrowid


def get_feedback_by_isbn(isbn: str) -> list[dict]:
    """특정 ISBN의 피드백 내역을 전부 조회한다."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM feedback WHERE isbn = ? ORDER BY created_at DESC",
            (isbn,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_all_feedback(limit: int = 500) -> list[dict]:
    """전체 피드백 내역을 최신 순으로 조회한다 (파인튜닝 데이터 추출용)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM feedback ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]
