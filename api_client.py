"""
frontend_streamlit/api_client.py
FastAPI 백엔드와 통신하는 함수 모음.
Streamlit 컴포넌트는 이 모듈만 통해 백엔드를 호출한다.
"""

from __future__ import annotations

import base64

import requests
import streamlit as st  
from streamlit.errors import StreamlitSecretNotFoundError

# FastAPI 기본 포트 8000
# 배포 시 st.secrets["backend"]["url"] 또는 환경변수로 오버라이드
def _resolve_base_url() -> str:
    """secrets.toml이 없어도 localhost로 안전 fallback."""
    try:
        backend = st.secrets.get("backend", {})
        return backend.get("url", "") or "http://localhost:8000"
    except (FileNotFoundError, StreamlitSecretNotFoundError, KeyError, AttributeError):
        return "http://localhost:8000"


_BASE = _resolve_base_url()


def _url(path: str) -> str:
    return f"{_BASE.rstrip('/')}/{path.lstrip('/')}"


def _default_timeout() -> int:
    return 120  # 알라딘·GPT 호출 포함해서 넉넉히


# ── MARC 변환 ────────────────────────────────────────────────

def convert_isbn(
    isbn: str,
    *,
    reg_mark: str = "",
    reg_no: str = "",
    copy_symbol: str = "",
    use_ai_940: bool = True,
) -> dict:
    """
    단일 ISBN을 MARC 레코드로 변환 요청.

    Returns:
        {
            "isbn": str,
            "mrk_text": str,
            "marc_bytes": bytes,   # base64 디코딩된 바이너리
            "meta": dict,
            "error": str | None,
        }
    """
    try:
        resp = requests.post(
            _url("/api/convert"),
            json={
                "isbn":        isbn,
                "reg_mark":    reg_mark,
                "reg_no":      reg_no,
                "copy_symbol": copy_symbol,
                "use_ai_940":  use_ai_940,
            },
            timeout=_default_timeout(),
        )
        resp.raise_for_status()
        data = resp.json()

        # FastAPI 응답: marc_bytes_b64(str) → marc_bytes(bytes) 변환
        b64 = data.pop("marc_bytes_b64", "") or ""
        data["marc_bytes"] = base64.b64decode(b64) if b64 else b""
        return data

    except requests.exceptions.Timeout:
        return {"isbn": isbn, "error": "⏱️ 요청 시간 초과 (백엔드 응답 없음)"}
    except requests.exceptions.ConnectionError:
        return {"isbn": isbn, "error": "🔌 백엔드 서버에 연결할 수 없습니다"}
    except Exception as e:
        return {"isbn": isbn, "error": f"❌ 변환 실패: {e}"}


def convert_batch(jobs: list[list]) -> list[dict]:
    """
    여러 ISBN을 일괄 변환 요청.

    Args:
        jobs: [[isbn, reg_mark, reg_no, copy_symbol], ...]
    """
    # FastAPI BatchRequest 스키마: {"jobs": [{"isbn":..., ...}, ...]}
    job_dicts = [
        {
            "isbn":        j[0],
            "reg_mark":    j[1] if len(j) > 1 else "",
            "reg_no":      j[2] if len(j) > 2 else "",
            "copy_symbol": j[3] if len(j) > 3 else "",
        }
        for j in jobs
    ]
    try:
        resp = requests.post(
            _url("/api/convert/batch"),
            json={"jobs": job_dicts},
            timeout=_default_timeout() * max(len(jobs), 1),
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        for r in results:
            b64 = r.pop("marc_bytes_b64", "") or ""
            r["marc_bytes"] = base64.b64decode(b64) if b64 else b""
        return results

    except requests.exceptions.Timeout:
        return [{"isbn": j[0], "error": "⏱️ 일괄 변환 시간 초과"} for j in jobs]
    except requests.exceptions.ConnectionError:
        return [{"isbn": j[0], "error": "🔌 백엔드 서버에 연결할 수 없습니다"} for j in jobs]
    except Exception as e:
        return [{"isbn": j[0], "error": f"❌ 일괄 변환 실패: {e}"} for j in jobs]


# ── 피드백 저장 ──────────────────────────────────────────────

def submit_feedback(
    isbn: str,
    field_tag: str,
    ai_value: str,
    corrected_value: str,
    librarian_note: str = "",
) -> bool:
    """
    사서의 수정값을 백엔드 DB에 저장.

    Returns:
        True  — 저장 성공
        False — 실패
    """
    try:
        resp = requests.post(
            _url("/api/feedback"),
            json={
                "isbn":            isbn,
                "field_tag":       field_tag,
                "ai_value":        ai_value,
                "corrected_value": corrected_value,
                "librarian_note":  librarian_note,
            },
            timeout=10,
        )
        return resp.ok
    except Exception:
        return False
