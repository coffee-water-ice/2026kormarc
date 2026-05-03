"""
backend_fastapi/app.py
FastAPI 애플리케이션 진입점.

엔드포인트:
  POST /api/convert        — 단일 ISBN → MARC 변환
  POST /api/convert/batch  — 다중 ISBN 일괄 변환
  POST /api/feedback       — 사서 수정값 DB 저장
  GET  /health             — 헬스체크
"""

from __future__ import annotations

import base64
import logging
import tomllib
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# 내부 모듈
from core.field_rules import build_260_field, build_300_field
from api.external_apis import build_pub_location_bundle, get_aladin_item_by_isbn
from database.feedback_logger import init_db, save_feedback_record

logger = logging.getLogger("isbn2marc")


# ============================================================
# Lifespan (앱 시작·종료 시 실행)
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 시작 시: DB 테이블 초기화
    init_db()
    logger.info("DB 초기화 완료")
    yield
    # 종료 시: 필요 시 리소스 정리
    logger.info("서버 종료")


# ============================================================
# FastAPI 앱 인스턴스
# ============================================================

app = FastAPI(
    title="ISBN → MARC 변환 API",
    description="알라딘·NLK·OpenAI를 활용한 KORMARC 자동 생성 백엔드",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — Streamlit 개발 서버 허용 (배포 시 origins 제한)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501", "http://127.0.0.1:8501"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# Pydantic 스키마
# ============================================================

class ConvertRequest(BaseModel):
    isbn: str = Field(
        ...,
        min_length=10,
        max_length=17,
        json_schema_extra={"example": "9788937462849"},
    )
    reg_mark:    str = Field(default="", description="등록기호")
    reg_no:      str = Field(default="", description="등록번호")
    copy_symbol: str = Field(default="", description="별치기호")
    use_ai_940:  bool = Field(default=True, description="940 생성에 AI 활용 여부")


class ConvertResult(BaseModel):
    isbn:          str
    mrk_text:      str
    marc_bytes_b64: str          # bytes → base64 인코딩 후 전송
    meta:          dict
    error:         Optional[str] = None


class BatchRequest(BaseModel):
    jobs: list[ConvertRequest]


class BatchResult(BaseModel):
    results: list[ConvertResult]


class FeedbackRequest(BaseModel):
    isbn:             str = Field(..., json_schema_extra={"example": "9788937462849"})
    field_tag:        str = Field(..., json_schema_extra={"example": "300"})
    ai_value:         str = Field(..., description="AI가 생성한 원본 값")
    corrected_value:  str = Field(..., description="사서가 수정한 최종 값")
    librarian_note:   str = Field(default="", description="선택 메모")


class FeedbackResult(BaseModel):
    status: str
    id:     Optional[int] = None


# ============================================================
# 헬퍼
# ============================================================

def _load_runtime_secrets() -> dict:
    """
    런타임 설정(secrets)을 .streamlit/secrets.toml 또는 환경변수에서 로드한다.
    배포 환경(Render 등)에서는 환경변수를 우선 사용한다.
    """
    import os

    # 1) secrets.toml 로드 시도
    path = Path(__file__).resolve().parent / ".streamlit" / "secrets.toml"
    data: dict = {}
    if path.exists():
        with path.open("rb") as f:
            loaded = tomllib.load(f)
        data = loaded if isinstance(loaded, dict) else {}

    # 2) 환경변수로 덮어쓰기 (배포 환경 우선)
    for key in ("ALADIN_TTB_KEY", "OPENAI_API_KEY", "NLK_CERT_KEY"):
        env_val = os.environ.get(key)
        if env_val:
            data[key] = env_val

    return data


def _run_conversion(req: ConvertRequest, secrets: dict) -> ConvertResult:
    """
    단일 ISBN 변환 핵심 로직.
    generate_all_oneclick()을 호출하고 결과를 ConvertResult로 래핑한다.
    """
    try:
        isbn = req.isbn.strip().replace("-", "")
        item, aladin_err = get_aladin_item_by_isbn(isbn, secrets)
        if aladin_err:
            return ConvertResult(
                isbn=isbn,
                mrk_text="",
                marc_bytes_b64="",
                meta={"isbn": isbn},
                error=aladin_err,
            )

        publisher_raw = (item or {}).get("publisher", "") or ""
        pubdate = (item or {}).get("pubDate", "") or ""
        pubyear = pubdate[:4] if len(pubdate) >= 4 else ""

        # ── 260 ──────────────────────────────────────────────
        bundle = build_pub_location_bundle(isbn, publisher_raw, secrets)
        tag_260, f_260 = build_260_field(
            place_display=bundle["place_display"],
            publisher_name=publisher_raw,
            pubyear=pubyear,
        )

        # ── 300 ──────────────────────────────────────────────
        tag_300, f_300 = build_300_field(item)

        meta = {
            "isbn": isbn,
            "aladin_title": (item or {}).get("title", ""),
            "publisher_raw": publisher_raw,
            "pubyear": pubyear,
            "tag_260": tag_260,
            "tag_300": tag_300,
            "bundle_source": bundle.get("source"),
            "debug_lines": bundle.get("debug", []),
        }

        # MRK 텍스트 조립 (실제로는 generate_all_oneclick 반환값 사용)
        mrk_text = "\n".join([tag_260, tag_300])
        marc_bytes = b""  # 실제로는 record.as_marc()

        return ConvertResult(
            isbn=isbn,
            mrk_text=mrk_text,
            marc_bytes_b64=base64.b64encode(marc_bytes).decode(),
            meta=meta,
        )

    except Exception as e:
        logger.exception(f"변환 오류: {req.isbn}")
        return ConvertResult(
            isbn=req.isbn,
            mrk_text="",
            marc_bytes_b64="",
            meta={},
            error=str(e),
        )


# ============================================================
# 엔드포인트
# ============================================================

@app.get("/health", tags=["운영"])
async def health():
    """서버 상태 확인."""
    return {"status": "ok"}


@app.post("/api/convert", response_model=ConvertResult, tags=["MARC 변환"])
async def convert_single(req: ConvertRequest):
    """
    단일 ISBN을 MARC 레코드로 변환한다.

    - **isbn**: ISBN-13 (하이픈 포함 가능)
    - **use_ai_940**: 940 필드 생성에 OpenAI 사용 여부
    """
    secrets = _load_runtime_secrets()
    result = _run_conversion(req, secrets)
    if result.error:
        raise HTTPException(status_code=500, detail=result.error)
    return result


@app.post("/api/convert/batch", response_model=BatchResult, tags=["MARC 변환"])
async def convert_batch(req: BatchRequest):
    """
    여러 ISBN을 일괄 변환한다. 일부 실패해도 나머지는 계속 처리한다.
    """
    secrets = _load_runtime_secrets()
    results = [_run_conversion(job, secrets) for job in req.jobs]
    return BatchResult(results=results)


@app.post("/api/feedback", response_model=FeedbackResult, tags=["피드백"])
async def feedback(req: FeedbackRequest):
    """
    사서가 수정한 필드값을 DB에 저장한다.
    취약 필드(300·056·653) 수정 내역이 파인튜닝 데이터로 활용된다.
    """
    try:
        record_id = save_feedback_record(
            isbn=req.isbn,
            field_tag=req.field_tag,
            ai_value=req.ai_value,
            corrected_value=req.corrected_value,
            librarian_note=req.librarian_note,
        )
        return FeedbackResult(status="ok", id=record_id)
    except Exception as e:
        logger.exception("피드백 저장 오류")
        raise HTTPException(status_code=500, detail=str(e))
    