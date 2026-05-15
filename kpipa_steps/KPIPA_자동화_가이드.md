# KPIPA 출판사 DB 자동화 시스템 가이드

구현 완료 기준: 2026-05-14

---

## 개요

KPIPA(한국출판인회의) 출판사 목록을 매주 자동으로 수집하여
Google Sheets `출판사 DB` 스프레드시트의 `발행처명–주소 연결표` 시트를 갱신한다.

- **수집 대상**: https://bnk.kpipa.or.kr/home/v3/addition/adiPblshrInfoList
- **갱신 대상**: Google Sheets `출판사 DB` > `발행처명–주소 연결표`
- **자동화 스케줄**: 매주 월요일 10:00 KST (GitHub Actions)
- **저장소 브랜치**: `master` (기본 브랜치)

---

## 파일 구성

| 파일 | 역할 |
|------|------|
| `kpipa_scraper.py` | KPIPA 크롤링 공통 모듈 (import 전용) |
| `kpipa_step1.py` | 최초 전체 수집 → Excel 저장 (1회성) |
| `kpipa_step2.py` | 기존 Excel에 신규 데이터 병합·중복 처리 (로컬용) |
| `kpipa_step3.py` | KPIPA 수집 + Google Sheets 갱신 (GitHub Actions 실행 파일) |
| `.github/workflows/kpipa_weekly.yml` | 주간 자동 실행 워크플로우 |
| `requirements-kpipa.txt` | 자동화 전용 의존성 |

---

## kpipa_scraper.py — 크롤링 공통 모듈

### 핵심 설계 결정

KPIPA 사이트는 JavaScript AJAX 방식으로 목록을 렌더링하므로 `requests`로는 데이터를 가져올 수 없다.
**Playwright(동기 API)**를 사용하여 실제 브라우저를 구동한다.

### 주요 함수

#### `fetch_all(until_no=1, max_pages=None) -> pd.DataFrame`

전체 출판사 목록을 수집하여 DataFrame 반환.

- 페이지 이동: `fnPblshrInfoList(N)` JavaScript 함수 호출
- 페이지 완료 신호: `li.fraction` 텍스트가 `"N / 전체"` 형식으로 바뀌는 것을 DOM에서 감지
  → `networkidle` 대신 이 방식을 사용해야 페이지 스킵 없이 안정적으로 수집됨
- 루프 상한: `초기_페이지수 + 50` (KPIPA 목록이 수집 중 갱신되어 페이지가 늘어날 수 있음)
- 중복 제거: 수집 후 `drop_duplicates(subset=["순번"])` 적용
- `max_pages`: 테스트용 제한 (예: `--pages 3`)

---

## kpipa_step3.py — Google Sheets 갱신 (GitHub Actions 실행 파일)

단독으로 전체 기능이 동작한다. `kpipa_step1.py`, `kpipa_step2.py` 없이도 실행 가능.

### 실행 방법

```bash
python kpipa_step3.py              # 실제 Sheets 반영
python kpipa_step3.py --dry-run    # 결과 미리보기만 (Sheets 반영 없음)
python kpipa_step3.py --pages 3    # 테스트: 3페이지만 수집
```

### 실행 흐름

```
1. kpipa_scraper.fetch_all() → 전체 출판사 목록 수집
2. Google Sheets에서 기존 데이터 읽기
3. 기존 데이터를 '구)발행처명–주소 연결표'에 전체 복사 (백업)
4. 기존 데이터 + 신규 데이터 병합
5. process_duplicates() → 비고 처리
6. KST 타임스탬프 추가 (갱신 컬럼)
7. 시트 일괄 반영 (500행씩 청크 업데이트)
```

### Google Sheets 컬럼 구조

| A: 순번 | B: 출판사명 | C: 지역 | D: 전화번호 | E: 비고 | F: 갱신 |
|---------|------------|---------|------------|--------|--------|

- **비고** 값: `신규 등록` / `유지` / `확인필요` / `기존`
- **갱신** 값: `갱신: YYYY-MM-DD HH:MM` (KST 기준)

> F열(갱신)은 2026-05-14 추가됨. 기존 5열 시트도 자동 인식하여 처리.

### 중복 처리 로직

중복 기준: **출판사명 + 지역 모두 일치** (복합 키)

| 상황 | 비고 |
|------|------|
| 신규 수집에만 있음 | `신규 등록` |
| 기존에만 있음 (최신 목록에서 사라짐) | `확인필요` |
| 양쪽 모두 있음 (출판사명·지역 동일) | `유지` (중복 행 제거) |

> 동명이지만 지역이 다른 출판사는 별개 법인으로 처리 → 별도 행 유지

### 기존 행 구분 처리

Sheets에서 읽어온 기존 데이터의 비고 컬럼이 비어 있으면
`_is_new` 플래그가 신규 행으로 잘못 판단하므로, 읽어온 직후 아래처럼 처리:

```python
old_df.loc[old_df["비고"] == "", "비고"] = "기존"
```

### Google Sheets 인증

우선순위:
1. 환경변수 `GSPREAD_CREDENTIALS` (JSON 문자열) — GitHub Actions용
2. `.streamlit/secrets.toml`의 `[gspread]` 섹션 — 로컬용

---

## kpipa_step2.py — 로컬 Excel 병합 (보조 스크립트)

GitHub Actions와 무관하게 로컬에서 Excel 파일 기반으로 데이터를 병합할 때 사용.

```bash
python kpipa_step2.py            # 최신 출판사정리_리스트_*.xlsx 자동 탐색 후 갱신
python kpipa_step2.py --pages 3  # 테스트용
```

- `my project` 폴더의 `출판사정리_리스트_*.xlsx` 중 가장 최신 파일을 자동으로 찾음
- 동일 날짜 파일이 이미 있으면 덮어씀

---

## GitHub Actions 워크플로우

### `.github/workflows/kpipa_weekly.yml`

```yaml
name: KPIPA DB 주간 갱신

on:
  schedule:
    - cron: '0 1 * * 1'    # 매주 월요일 01:00 UTC = 10:00 KST
  workflow_dispatch:         # GitHub UI에서 수동 실행 가능

jobs:
  update-publisher-db:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4.2.2

      - name: Python 설정
        uses: actions/setup-python@v5.6.0
        with:
          python-version: '3.11'

      - name: 의존성 설치
        run: pip install -r requirements-kpipa.txt

      - name: Playwright 브라우저 설치
        run: playwright install chromium --with-deps

      - name: KPIPA DB 갱신 실행
        env:
          GSPREAD_CREDENTIALS: ${{ secrets.GSPREAD_CREDENTIALS }}
        run: python kpipa_step3.py
```

### GitHub 설정 사항

- **기본 브랜치**: `master` (GitHub Actions는 기본 브랜치의 워크플로우만 실행)
- **필수 Secret**: `GSPREAD_CREDENTIALS`

### GSPREAD_CREDENTIALS 시크릿 형식

`.streamlit/secrets.toml`의 `[gspread]` 섹션을 **한 줄 JSON**으로 변환하여 등록.
`private_key` 내의 줄바꿈은 반드시 `\n` (이스케이프 시퀀스)으로 대체해야 함.

```json
{"type":"service_account","project_id":"...","private_key_id":"...","private_key":"-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----\n","client_email":"...@....iam.gserviceaccount.com","client_id":"...","token_uri":"https://oauth2.googleapis.com/token"}
```

Secret 등록 위치: GitHub 리포지토리 → Settings → Secrets and variables → Actions → New repository secret

---

## 로컬 실행 방법

### 주의: Windows 환경

```powershell
# Windows Store Python 대신 가상환경 Python 사용
.\.venv\Scripts\python.exe kpipa_step3.py

# 한국어 출력 깨짐 방지 (cp949 인코딩 오류 대응)
$env:PYTHONIOENCODING = "utf-8"
.\.venv\Scripts\python.exe kpipa_step3.py
```

### 의존성 설치

```bash
pip install -r requirements-kpipa.txt
playwright install chromium --with-deps
```

---

## 알려진 이슈 및 대응

| 이슈 | 원인 | 대응 |
|------|------|------|
| Google Sheets `Range exceeds grid limits` | 결과 행이 시트 기본 행 수(5000)를 초과 | `ws_main.resize(rows=needed_rows)` 호출 |
| `DeprecationWarning: update() positional args` | gspread API 변경 | `range_name=`, `values=` 명명 인수 사용 |
| 신규 7000건, 확인필요 0건 | 기존 행의 비고가 비어 신규로 오인 | 읽기 직후 빈 비고를 `"기존"`으로 채움 |
| Windows `UnicodeEncodeError: cp949` | 터미널 인코딩 | `PYTHONIOENCODING=utf-8` 설정 |
| `python` 명령 exit code 49 | Windows Store Python 플레이스홀더 | `.venv\Scripts\python.exe` 사용 |
| KPIPA 페이지 스킵 | `networkidle` 대기 부정확 | `li.fraction` DOM 텍스트 변화를 신호로 사용 |
