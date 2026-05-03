# ISBN -> MARC Service

ISBN 입력으로 MARC 관련 필드를 생성하는 FastAPI + Streamlit 프로젝트.

## 구성
- `app.py`: FastAPI 백엔드 엔트리
- `streamlit_app.py`: Streamlit 프론트 엔트리
- `api_client.py`: 프론트에서 백엔드 호출
- `core/`: MARC 생성 규칙/유틸
- `api/`: 외부 API 연동(알라딘, KPIPA, 문체부, Google Sheets)
- `database/`: 피드백 SQLite 저장 로직

## 실행 방법

### 1) 백엔드 실행
```bash
uvicorn app:app --reload
```

### 2) 프론트 실행
```bash
streamlit run streamlit_app.py
```

## 접속 URL
- FastAPI Health: `http://127.0.0.1:8000/health`
- FastAPI Docs: `http://127.0.0.1:8000/docs`
- Streamlit UI: `http://localhost:8501`

## 필수 설정
프로젝트 루트의 `.streamlit/secrets.toml`에 아래 키가 필요하다.

- `[backend]`
  - `url` (예: `http://localhost:8000`)
- `ALADIN_TTB_KEY`
- `[gspread]` 서비스 계정 정보
- (선택) `OPENAI_API_KEY`, `NLK_CERT_KEY`, `[kpipa]`

## 동작 흐름
1. Streamlit에서 ISBN 입력
2. `api_client.py`가 `/api/convert` 호출
3. 백엔드가 알라딘 API로 도서 정보 조회
4. 출판사명 기반으로 발행지 소스(KPIPA/문체부/구글시트) 판단
5. 260/300 필드 생성 후 JSON 응답 반환
