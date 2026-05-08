# ISBN → MARC 변환 서비스 — 기술 문서

## 프로젝트 개요

ISBN을 입력받아 도서관 목록 레코드에 필요한 **MARC 260/300 필드**를 자동 생성하는 서비스.

- **백엔드**: FastAPI (Python) — 비즈니스 로직, 외부 API 연동
- **프론트엔드**: Streamlit — ISBN 입력 UI, 결과 표시
- **배포**: Render (`https://two026kormarc.onrender.com`)

---

## 디렉토리 구조

```
my project/
├── app.py                  # FastAPI 백엔드 진입점
├── streamlit_app.py        # Streamlit 프론트 진입점
├── api_client.py           # 프론트 → 백엔드 HTTP 클라이언트
│
├── core/
│   ├── marc_builder.py     # pymarc.Record ↔ MRK 텍스트 변환 유틸
│   └── field_rules.py      # 260/300 필드 생성 규칙
│
├── api/
│   └── external_apis.py    # 알라딘 / KPIPA / 문체부 / Google Sheets 연동
│
├── database/
│   └── feedback_logger.py  # 사서 피드백 SQLite 저장
│
├── feedback.db             # SQLite 피드백 데이터베이스
├── requirements.txt        # Python 의존성
└── .streamlit/
    └── secrets.toml        # API 키 및 서비스 계정 설정 (git 제외)
```

---

## 전체 요청 흐름

```
[사용자]
  └── ISBN 입력 (Streamlit UI)
        │
        ▼
[streamlit_app.py]
  └── api_client.convert_isbn(isbn) 호출
        │  POST /api/convert
        ▼
[app.py — FastAPI]
  └── _run_conversion(req, secrets)
        ├── 1. ISBN 정규화 (하이픈 제거, 13자리 검증)
        ├── 2. get_aladin_item_by_isbn()
        │       → 알라딘 TTB API → 제목, 출판사, 출판일, 상세페이지 URL
        ├── 3. build_pub_location_bundle()
        │       → KPIPA 웹 스크래핑
        │       → Google Sheets 출판사 DB 조회
        │       → 문체부(MCST) 웹 스크래핑
        │       → 발행지 문자열 + MARC 008 국가코드 확정
        ├── 4. build_260_field()  → "=260  \\$a서울 :$b민음사,$c2023"
        └── 5. build_300_field()
                → 알라딘 상세 페이지 스크래핑 (쪽수, 크기, 삽화)
                → "=300  \\$a425 p. :$billustrations ;$c24 cm"
        │
        ▼
  JSON 응답 { mrk_text, marc_bytes(base64), metadata }
        │
        ▼
[api_client.py]
  └── base64 디코드 → MARC 바이너리
        │
        ▼
[streamlit_app.py]
  └── MRK 텍스트 코드블록 표시
      메타데이터 JSON 표시
```

---

## 모듈별 상세 설명

### `app.py` — FastAPI 백엔드

**역할**: 요청 수신, 변환 파이프라인 조율, 응답 직렬화

**주요 Pydantic 스키마**

| 스키마 | 필드 | 설명 |
|---|---|---|
| `ConvertRequest` | isbn, reg_mark, reg_no, copy_symbol, use_ai_940 | 단건 변환 요청 |
| `ConvertResult` | mrk_text, marc_bytes, metadata, errors | 변환 결과 |
| `BatchRequest` | isbns (최대 50건) | 일괄 변환 요청 |
| `FeedbackRequest` | isbn, field_tag, ai_value, corrected_value, librarian_note | 사서 수정 피드백 |

**엔드포인트**

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/health` | 서버 상태 확인 |
| POST | `/api/convert` | ISBN 단건 변환 |
| POST | `/api/convert/batch` | ISBN 일괄 변환 (부분 실패 허용) |
| POST | `/api/feedback` | 사서 피드백 저장 |

**시크릿 로딩**: `_load_runtime_secrets()` — `.streamlit/secrets.toml` 또는 환경변수 중 가용한 쪽 사용

---

### `api_client.py` — Streamlit HTTP 클라이언트

**역할**: Streamlit 프론트에서 백엔드 호출을 캡슐화

**주요 함수**

| 함수 | 설명 |
|---|---|
| `_resolve_base_url()` | `st.secrets`에서 백엔드 URL 로드, 없으면 `http://localhost:8000` fallback |
| `convert_isbn(isbn, ...)` | 단건 변환. 타임아웃·연결 오류를 사용자 친화 메시지로 변환 |
| `convert_batch(isbns, ...)` | 일괄 변환. ISBN 수에 따라 타임아웃 자동 스케일 |
| `submit_feedback(...)` | 사서 피드백 백엔드 전송 |

**오류 표시**: ❌ 서버 오류 / 🔌 연결 불가 / ⏱️ 타임아웃

---

### `core/marc_builder.py` — MARC 변환 유틸

**역할**: pymarc 라이브러리와 사람이 읽을 수 있는 MRK 텍스트 포맷 간 변환

**`MarcBuilder` 클래스**

```python
builder = MarcBuilder()
builder.add_ctl("001", "ISBN-1234567890")
builder.add("260", " ", " ", [("a", "서울 :"), ("b", "민음사,"), ("c", "2023")])
mrk = builder.mrk_lines   # ["=001  ISBN-1234567890", "=260  \\$a서울 :$b민음사,$c2023"]
record = builder.record    # pymarc.Record 객체
```

**`mrk_str_to_field(mrk_line)`**: MRK 한 줄 → `pymarc.Field`
- 제어 필드 (태그 < 10): 값만 있음
- 데이터 필드: 지시기호 2자 + `$` 구분자 + 서브필드

**`record_to_mrk(record)`**: `pymarc.Record` → MRK 텍스트 전체

---

### `core/field_rules.py` — 260/300 필드 생성

**역할**: 알라딘 조회 데이터와 발행지 판단 결과로 MARC 필드 생성

#### 260 필드 (발행사항)

```
=260  \\$a서울 :$b민음사,$c2023
       ^^  ─┬─  ──┬───  ──┬──
            $a   $b       $c
         발행지  발행처   발행년도
```

`build_260_field(pub_location, publisher, pub_year)` → `(mrk_str, pymarc.Field)`

#### 300 필드 (형태사항)

```
=300  \\$a425 p. :$billustrations, photographs ;$c24 cm
            ─┬──   ───────────┬──────────────   ──┬──
             $a               $b                  $c
           쪽수              삽화 정보            크기
```

**알라딘 상세페이지 스크래핑 순서**:
1. `_fetch_aladin_detail_page(url)` — HTTP GET, HTML 반환
2. `_parse_aladin_physical_info(html)` — BeautifulSoup으로 파싱
   - 쪽수: "xxx쪽" 또는 "xxxp" 패턴 추출
   - 크기: mm 단위 → cm 변환 (반올림)
   - 삽화: 알라딘 텍스트 내 키워드 매핑

**삽화 키워드 매핑 예시**

| 입력 키워드 | MARC 값 |
|---|---|
| 컬러, 천연색 | color illustrations |
| 사진, photo | photographs |
| 지도, 地圖 | maps |
| 도표, chart | charts |

---

### `api/external_apis.py` — 외부 API 연동

**역할**: 출판사 발행지 다중 소스 조회 (MARC 260 $a 확정)

#### 알라딘 API

```python
item = get_aladin_item_by_isbn(isbn, ttb_key)
# 반환: { title, publisher, pubDate, link, cover }
```

TTB API에 ISBN 쿼리 → JSON 파싱 → item dict 반환

#### Google Sheets 출판사 DB

**`load_publisher_db(creds)`** — 3개 시트 로드:

| 시트명 | 내용 | 용도 |
|---|---|---|
| 발행처명–주소 연결표 | 출판사명 → 주소 | 260 $a 발행지 |
| 발행국명–발행국부호 연결표 | 국가명 → 3자리 코드 | MARC 008 국가코드 |
| 발행처-임프린트 연결표 | 임프린트 → 모회사 | 임프린트 fallback |

#### 출판사명 정규화 파이프라인

```
원본: "㈜민음사(MinumSa)"
  │
  ▼ normalize_publisher_name()
  │  → 공백 제거, ㈜/주식회사/() 제거, 소문자화
  ▼ "민음사minumsa"
  │
  ▼ normalize_stage2()
  │  → 영문 부분 제거, 시리즈 접미사 제거
  ▼ "민음사"
```

#### 발행지 통합 판단 — `build_pub_location_bundle()`

**우선순위 체인 (실패 시 다음으로)**:

```
1순위: KPIPA ISBN 조회
       get_publisher_name_from_isbn_kpipa(isbn)
       → bnk.kpipa.or.kr 스크래핑
       
2순위: Google Sheets DB 직접 검색
       search_publisher_location_with_alias(publisher, db)
       
3순위: 임프린트 → 모회사 경로
       find_main_publisher_from_imprints(publisher, imprint_db)
       
4순위: 문체부(MCST) 스크래핑
       get_mcst_address()
       → book.mcst.go.kr 검색 (영업 상태 필터링)
       
기본값: "출판지 미상"
```

**반환값**:

```python
{
    "raw_address": "서울특별시 강남구 ...",
    "display": "서울",           # normalize_publisher_location_for_display() 적용
    "country_code": "ko ",       # MARC 008용 3자리+공백
    "source": "GoogleSheets",    # 판단 근거
    "debug": ["...", "..."]      # 내부 판단 로그
}
```

**발행지 표시명 변환**: 전체 주소 → 광역시/도 단위 도시명

| 주소 포함 | 표시명 |
|---|---|
| 서울 | 서울 |
| 인천 | 인천 |
| 대전 | 대전 |
| 대구 | 대구 |
| 부산 | 부산 |
| 광주 | 광주 |
| 울산 | 울산 |
| 경기 | 경기 |

---

### `database/feedback_logger.py` — 피드백 저장

**역할**: 사서가 수정한 MARC 값을 학습 데이터로 SQLite에 저장

**스키마**

```sql
CREATE TABLE feedback (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    isbn             TEXT NOT NULL,
    field_tag        TEXT NOT NULL,       -- "260", "300" 등
    ai_value         TEXT NOT NULL,       -- 시스템 생성값
    corrected_value  TEXT NOT NULL,       -- 사서 수정값
    librarian_note   TEXT NOT NULL,       -- 비고
    created_at       TEXT NOT NULL        -- ISO 8601 UTC
)
```

**함수**

| 함수 | 설명 |
|---|---|
| `init_db()` | 테이블 없으면 생성 (앱 시작 시 호출) |
| `save_feedback_record(...)` | 피드백 행 삽입, 생성된 id 반환 |
| `get_feedback_by_isbn(isbn)` | ISBN별 피드백 전체 조회 |
| `get_all_feedback(limit=100)` | 최신 N건 조회 (ML 학습용) |

DB 경로: 환경변수 `FEEDBACK_DB_PATH` 또는 `./feedback.db`

---

## 설정 가이드

### `.streamlit/secrets.toml` 구조

```toml
ALADIN_TTB_KEY = "ttb..."          # 필수 — 알라딘 TTB 키
OPENAI_API_KEY = "sk-..."          # 선택 — 940 필드 AI 생성 시
NLK_CERT_KEY   = "..."             # 선택 — 국립중앙도서관 인증키

[backend]
url = "https://two026kormarc.onrender.com"   # 배포 시
# url = "http://localhost:8000"              # 로컬 개발 시

[gspread]                          # 필수 — Google 서비스 계정
type             = "service_account"
project_id       = "..."
private_key_id   = "..."
private_key      = "-----BEGIN RSA PRIVATE KEY-----\n..."
client_email     = "...@....iam.gserviceaccount.com"
client_id        = "..."
token_uri        = "https://oauth2.googleapis.com/token"

[kpipa]
session_id = "..."                 # 선택 — KPIPA 세션 쿠키
```

### 환경변수 (Render 배포 시)

`.streamlit/secrets.toml` 대신 아래 환경변수 설정:

| 변수 | 내용 |
|---|---|
| `ALADIN_TTB_KEY` | 알라딘 TTB 키 |
| `OPENAI_API_KEY` | OpenAI 키 |
| `NLK_CERT_KEY` | NLK 인증키 |
| `GSPREAD_CREDENTIALS` | gspread 섹션 JSON 문자열 |
| `FEEDBACK_DB_PATH` | DB 파일 경로 |

---

## 로컬 실행 방법

```bash
# 의존성 설치
pip install -r requirements.txt

# 백엔드 실행 (터미널 1)
uvicorn app:app --reload

# 프론트 실행 (터미널 2)
streamlit run streamlit_app.py
```

| 서비스 | URL |
|---|---|
| FastAPI Swagger | http://127.0.0.1:8000/docs |
| FastAPI Health | http://127.0.0.1:8000/health |
| Streamlit UI | http://localhost:8501 |

---

## 의존성 요약

| 패키지 | 용도 |
|---|---|
| fastapi / uvicorn | 백엔드 API 서버 |
| streamlit | 프론트 UI |
| pydantic | 요청/응답 데이터 검증 |
| pymarc | MARC 레코드 객체 생성 |
| requests | HTTP 클라이언트 (알라딘, KPIPA, 문체부) |
| beautifulsoup4 | HTML 파싱 (알라딘 상세, KPIPA, 문체부) |
| gspread / oauth2client | Google Sheets 출판사 DB |
| openai | (선택) 940 필드 AI 생성 |
| python-dotenv | 환경변수 로드 |
| pandas | 데이터 처리 |
