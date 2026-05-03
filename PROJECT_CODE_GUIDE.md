# ISBN -> MARC 프로젝트 코드 정리 문서

이 문서는 현재 프로젝트의 코드 역할과 실행 흐름을 정리하고, 유지에 필수인 부분과 정리(삭제/통합) 권장 부분을 함께 제시한다.

## 1) 현재 구조와 파일 역할

### 실행/진입점
- `app.py`
  - FastAPI 백엔드 서버 진입점.
  - 엔드포인트:
    - `POST /api/convert`
    - `POST /api/convert/batch`
    - `POST /api/feedback`
    - `GET /health`
  - ISBN 입력을 받아 알라딘 조회 -> 발행지 판단 -> 260/300 생성 -> 응답 반환.

- `streamlit_app.py`
  - Streamlit 프론트 UI 진입점.
  - ISBN 입력을 받아 `api_client.py`를 통해 백엔드 `/api/convert` 호출.

- `api_client.py`
  - Streamlit 프론트에서 백엔드 호출을 담당하는 HTTP 클라이언트 모듈.
  - `convert_isbn`, `convert_batch`, `submit_feedback` 제공.

### 도메인/비즈니스 로직
- `core/marc_builder.py`
  - `pymarc.Record` <-> MRK 텍스트 변환 관련 유틸.
  - `mrk_str_to_field`, `record_to_mrk`, `MarcBuilder`.

- `core/field_rules.py`
  - 260/300 생성 규칙.
  - `build_260_field`, `build_300_field`, `build_300_mrk` 제공.

- `api/external_apis.py`
  - 외부 데이터 연계:
    - 알라딘 API 조회
    - KPIPA/문체부 조회
    - Google Sheets 기반 출판사 DB 조회
  - 발행지 통합 판단 함수 `build_pub_location_bundle`.

- `database/feedback_logger.py`
  - 피드백 SQLite 저장/조회 로직.
  - `feedback.db` 사용.

### 보조/예시 파일
- `_260_300_usage_example.py`
  - 260/300 생성 흐름 예시용 코드.
  - 실운영 엔드포인트에서 직접 import하지 않아도 동작에는 영향 없음.

- `main.py`
  - 간단한 FastAPI hello 샘플.
  - 현재 실서비스 경로(`app.py`)와 별개.


## 2) 실제 동작 흐름 (요청 의도 기준)

1. 사용자가 `streamlit_app.py`에서 ISBN 입력  
2. `api_client.py`가 `/api/convert` 호출  
3. `app.py`가 ISBN 정규화 후 알라딘 API에서 도서 item 조회  
4. 조회된 출판사명으로 `build_pub_location_bundle` 실행  
   - Google Sheets DB, KPIPA, 문체부 경로를 이용해 발행지 판단  
5. `core/field_rules.py`에서 260/300 생성  
6. 결과(MRK 텍스트, 메타)를 Streamlit에 표시


## 3) 필수적으로 유지해야 할 부분

아래는 서비스 동작에 필수:

- `app.py` (백엔드 API 서버)
- `streamlit_app.py` (프론트 UI)
- `api_client.py` (프론트-백엔드 통신)
- `core/` 패키지 (`marc_builder.py`, `field_rules.py`)
- `api/external_apis.py` (알라딘/발행지 연계)
- `database/feedback_logger.py` + `feedback.db`
- `.streamlit/secrets.toml` 또는 동등한 secrets 파일
  - 최소 권장 키:
    - `backend.url`
    - `ALADIN_TTB_KEY`
    - `[gspread]` 서비스 계정 정보


## 4) 정리(삭제/통합) 권장 항목

### A. 삭제 후보 (운영 영향 낮음)
- `main.py`
  - 현재 서비스와 무관한 샘플 엔드포인트.
- `_260_300_usage_example.py`
  - 설명용 예시 파일. 별도 문서로 대체 가능.

### B. 통합/이름 정리 권장
- `screts.toml` (오타 파일명)
  - `secrets.toml` 또는 `.streamlit/secrets.toml`로 통합 권장.
  - 같은 값이 분산되면 추후 설정 충돌/누락 위험이 커짐.

### C. 즉시 개선 권장
- `api_client.py`의 `_BASE` 로딩
  - 현재 `st.secrets` 직접 접근 구조는 파일 누락 시 예외를 유발할 수 있음.
  - 안전한 fallback(`http://localhost:8000`) 로직 유지 권장.


## 5) 운영 시 최소 실행 절차

1. 백엔드 실행
```bash
uvicorn app:app --reload
```

2. 프론트 실행
```bash
streamlit run streamlit_app.py
```

3. 확인 URL
- FastAPI Health: `http://127.0.0.1:8000/health`
- FastAPI Docs: `http://127.0.0.1:8000/docs`
- Streamlit UI: `http://localhost:8501`


## 6) 권장 다음 정리 순서

1. secrets 파일 1개 체계로 통합 (`.streamlit/secrets.toml` 권장)  
2. `main.py`, `_260_300_usage_example.py` 유지 여부 결정  
3. `api_client.py` 안전 fallback 최종 점검  
4. `README`에 실행법/구성도/필수 키 목록 반영

