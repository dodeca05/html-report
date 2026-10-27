# HTML Report Server

Claude Code, OpenAI Codex 같은 AI 코딩 에이전트가 만든 HTML 보고서를 HTTP API로 업로드하고, 브라우저에서 바로 열어볼 수 있게 해주는 가벼운 FastAPI 서비스입니다.

- 데이터베이스 없음: 보고서는 `./data/reports/` 에 `.html` 파일 그대로 저장되고, 목록은 디렉터리를 스캔해 만듭니다.
- 업로드한 원본 HTML을 그대로 제공하며, CSS·JavaScript·SVG·Chart.js 같은 시각화도 동작합니다.
- 업로드된 HTML은 CSP sandbox로 격리됩니다. 스크립트는 실행되지만 서비스의 다른 페이지, 쿠키, 저장소에는 접근할 수 없습니다.
- 올린 보고서는 같은 주소를 유지한 채 교체(`PUT`)하거나 삭제(`DELETE`)할 수 있습니다.
- 보고서 목록은 사람용 페이지(`/index`)와 프로그램용 JSON API(`GET /api/docs`)로 모두 조회할 수 있습니다.

```text
에이전트 ──POST /api/docs──▶ 서버 ──▶ ./data/reports/report.html
                     ◀── {"url": "https://example.com/docs/report", ...}
사용자 ──GET /docs/report──▶ 원본 HTML (sandbox 격리)
사용자 ──GET /index──▶ 보고서 목록
```

## 빠른 시작

### 로컬 실행

Python 3.12 이상이 필요합니다.

```bash
cd html-report-server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# .env 의 UPLOAD_API_KEY 를 반드시 바꾸세요. 예: python3 -c "import secrets; print(secrets.token_urlsafe(32))"

uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- 현재 디렉터리의 `.env` 는 서버 시작 시 자동으로 읽습니다. 이미 설정된 환경변수가 우선합니다.
- `python -m app` 으로 실행하면 `.env`/환경변수의 `HOST`, `PORT` 로 바인딩합니다.
- 브라우저에서 `http://localhost:8000/index` 를 열면 목록 페이지가 보입니다. Swagger 문서는 `http://localhost:8000/api/swagger` 에 있습니다.

### Docker 실행

```bash
docker build -t html-report-server .

docker run -d \
  --name html-report-server \
  -p 8000:8000 \
  -v ./data/reports:/app/data/reports \
  --env-file .env \
  html-report-server
```

- 컨테이너 포트는 8000이고, 보고서 디렉터리 `/app/data/reports` 를 볼륨으로 영속화합니다. 컨테이너를 지우고 다시 만들어도 보고서는 유지됩니다.
- 컨테이너는 UID 1000의 비루트 사용자로 실행됩니다. 호스트 사용자의 UID가 다르면 바인드 마운트한 디렉터리에 쓰지 못하므로 `docker build --build-arg APP_UID=$(id -u) -t html-report-server .` 로 빌드하세요.
- Docker 23 이전 버전은 상대 경로 바인드 마운트를 지원하지 않습니다. 이 경우 `-v "$(pwd)/data/reports:/app/data/reports"` 를 사용하세요.
- 이미지에 `HEALTHCHECK` 가 포함되어 있어 `docker ps` 에서 상태(healthy)를 확인할 수 있습니다.

## 환경변수

| 이름 | 기본값 | 설명 |
|---|---|---|
| `HOST` | `0.0.0.0` | 바인딩 주소 (`python -m app`, Docker) |
| `PORT` | `8000` | 바인딩 포트 (`python -m app`, Docker). Host 헤더를 신뢰할 수 없을 때 대체 URL에도 사용 |
| `PUBLIC_BASE_URL` | (없음) | 응답 `url` 을 만들 때 쓰는 외부 공개 주소. 예: `https://example.com`. 운영 환경에서는 반드시 설정 |
| `REPORTS_DIR` | `./data/reports` | 보고서 저장 디렉터리. 없으면 시작 시 자동 생성 |
| `MAX_UPLOAD_SIZE_MB` | `10` | 최대 업로드 크기(MB, 소수 가능) |
| `UPLOAD_API_KEY` | (없음) | 업로드 API 키. **설정하지 않으면 업로드 API가 비활성화(503)** 됩니다 |
| `TZ` | `Asia/Seoul` | 목록 페이지와 `uploaded_at` 의 시간대 |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1,::1` | (선택) `PUBLIC_BASE_URL` 이 없을 때 응답 URL에 그대로 써도 되는 Host 이름 목록. `*` 는 모두 허용 |
| `LOG_LEVEL` | `INFO` | (선택) `DEBUG`, `INFO`, `WARNING`, `ERROR` |

`.env` 파일은 Docker `--env-file` 과 같은 `KEY=VALUE` 형식입니다. 줄 끝 주석은 지원하지 않습니다.

## API 사용 예제

### 업로드 — `POST /api/docs`

```bash
curl -X POST http://localhost:8000/api/docs \
  -H "X-API-Key: YOUR_API_KEY" \
  -F "file=@report.html"
```

파일 이름을 지정해서 업로드:

```bash
curl -X POST http://localhost:8000/api/docs \
  -H "X-API-Key: YOUR_API_KEY" \
  -F "file=@report.html" \
  -F "name=weekly-analysis"
```

응답 (`201 Created`):

```json
{
  "filename": "weekly-analysis.html",
  "url": "http://localhost:8000/docs/weekly-analysis",
  "uploaded_at": "2026-10-27T23:00:00+09:00"
}
```

| 필드 | 설명 |
|---|---|
| `filename` | 실제 저장된 파일명 |
| `url` | 브라우저로 바로 열 수 있는 절대 URL (`http://` 또는 `https://`). 한글·공백은 퍼센트 인코딩됨 |
| `uploaded_at` | 업로드 완료 시각 (ISO 8601, `TZ` 기준). 목록 페이지의 날짜와 같은 값(파일 mtime) |

업로드 규칙:

- `multipart/form-data` 의 `file` 필드에 `.html` 파일을 보냅니다. 업로드 파일의 확장자가 `.html` 이 아니면(대소문자 무관) 거부합니다.
- `name` 필드가 있으면 그 이름으로, 없으면 원본 파일 이름으로 저장합니다. 확장자는 `.html` 소문자로 정규화합니다.
  - `Report.HTML` → `Report.html`, `name=weekly` → `weekly.html`, `name=weekly.html` → `weekly.html`
- 한글과 공백을 쓸 수 있습니다: `name=주간 보고서` → `주간 보고서.html`, URL은 `/docs/%EC%A3%BC%EA%B0%84%20%EB%B3%B4%EA%B3%A0%EC%84%9C`
- 같은 이름의 보고서가 이미 있으면 `409 Conflict` 를 반환하고 기존 파일은 그대로 둡니다. 내용을 바꾸려면 아래의 `PUT` 을 사용하세요.
- 내용은 UTF-8 인코딩이어야 합니다. 보고서는 `text/html; charset=utf-8` 로 제공됩니다.

상태 코드 (오류 응답 형식: `{"detail": "..."}`):

| 코드 | 의미 |
|---|---|
| `200` | 목록 조회 성공 (`GET /api/docs`) |
| `201` | 업로드 성공 (`POST`) |
| `200` | 교체 성공 (`PUT`) |
| `204` | 삭제 성공 (`DELETE`, 본문 없음) |
| `400` | 허용되지 않는 이름(`/`, `\`, `..` 등), 빈 파일, UTF-8이 아닌 파일 |
| `401` | `X-API-Key` 누락 또는 불일치 |
| `404` | 교체·삭제할 보고서가 없음 (`PUT`, `DELETE`) |
| `409` | 같은 이름의 보고서가 이미 존재 (`POST`) |
| `413` | `MAX_UPLOAD_SIZE_MB` 초과 |
| `415` | `.html` 이 아닌 파일 |
| `422` | `file` 필드 누락 |
| `503` | 서버에 `UPLOAD_API_KEY` 가 설정되지 않아 업로드·교체·삭제 API가 비활성화됨 |

### 교체 — `PUT /api/docs/{name}`

이미 올린 보고서의 내용을 새 파일로 바꿉니다. 보고서 주소는 그대로이고, 업로드 날짜는 교체한 시각으로 바뀌어 목록 맨 위로 올라갑니다.

```bash
curl -X PUT http://localhost:8000/api/docs/weekly-analysis \
  -H "X-API-Key: YOUR_API_KEY" \
  -F "file=@report-v2.html"
```

- 응답은 업로드와 같은 형식(`filename`, `url`, `uploaded_at`)이며 상태 코드는 `200` 입니다.
- 보고서가 없으면 `404` 를 반환하고 새로 만들지 않습니다. 새 보고서는 `POST` 로 올리세요.
- 파일 규칙(`.html`, UTF-8, 크기 제한)은 업로드와 같습니다. 거부되면 기존 보고서는 그대로 남습니다.
- 새 파일을 임시 파일에 다 쓴 뒤 `os.replace` 로 한 번에 바꿉니다. 교체 중에 열어도 이전 내용이나 새 내용 중 하나가 온전히 보입니다.
- 이전 버전은 보관하지 않습니다.
- 한글·공백 이름은 URL 인코딩해서 보냅니다: `/api/docs/%EC%A3%BC%EA%B0%84%20%EB%B3%B4%EA%B3%A0%EC%84%9C`

### 삭제 — `DELETE /api/docs/{name}`

```bash
curl -X DELETE http://localhost:8000/api/docs/weekly-analysis \
  -H "X-API-Key: YOUR_API_KEY"
```

성공하면 본문 없이 `204` 를 반환하고, 목록과 `/docs/{name}` 에서 바로 사라집니다. 보고서가 없으면 `404` 입니다. 삭제한 이름으로는 다시 `POST` 업로드할 수 있습니다.

### 보고서 확인 — `GET /docs/{name}`

```text
http://localhost:8000/docs/report
http://localhost:8000/docs/weekly-analysis
```

URL의 `{name}` 에 `.html` 을 붙인 파일을 원본 그대로 반환합니다. 파일이 없으면 `404` 입니다. URL에는 확장자를 붙이지 않습니다(`/docs/report.html` 은 `report.html.html` 을 찾습니다).

### 보고서 목록 — `GET /index`

```text
http://localhost:8000/index
```

업로드된 보고서를 최신순으로 보여줍니다. 이름을 누르면 해당 보고서로 이동합니다. `GET /` 는 `/index` 로 리다이렉트됩니다.

### 보고서 목록 JSON — `GET /api/docs`

프로그램이나 AI 에이전트가 목록을 조회할 때 씁니다. `/index` 와 같은 내용(같은 정렬·같은 대상)이며, 인증 없이 공개됩니다.

```bash
curl http://localhost:8000/api/docs
```

```json
{
  "count": 1,
  "reports": [
    {
      "name": "weekly-analysis",
      "filename": "weekly-analysis.html",
      "url": "http://localhost:8000/docs/weekly-analysis",
      "size": 48213,
      "uploaded_at": "2026-10-27T23:00:00+09:00"
    }
  ]
}
```

| 필드 | 설명 |
|---|---|
| `count` | 보고서 개수 |
| `reports` | 최신순 목록. 보고서가 없으면 빈 배열 |
| `name` | 보고서 이름 (`.html` 제외). `/docs/{name}`, `PUT`/`DELETE /api/docs/{name}` 에 사용 |
| `filename` | 저장된 파일명 |
| `url` | 브라우저로 바로 열 수 있는 절대 URL (업로드 응답과 같은 규칙으로 생성) |
| `size` | 파일 크기 (바이트) |
| `uploaded_at` | 업로드 날짜 (파일 mtime, ISO 8601, `TZ` 기준) |

### API 문서

FastAPI 기본 문서 경로 `/docs` 는 보고서 URL과 겹치므로 Swagger UI를 `/api/swagger` 로 옮겼습니다. OpenAPI 스키마는 `/api/openapi.json` 에 있습니다.

## AI 에이전트에서 사용하기

`CLAUDE.md` (Claude Code) 또는 `AGENTS.md` (Codex)에 다음과 같이 적어두면 에이전트가 보고서를 만든 뒤 직접 업로드하고 링크를 알려줍니다. API 키는 문서에 직접 쓰지 말고 환경변수로 넘기세요.

````markdown
## HTML 보고서 공유
HTML 보고서를 만들었다면 아래 명령으로 업로드하고, 응답 JSON의 `url` 을 사용자에게 알려주세요.
이미 올라간 보고서는 `curl -sS "$REPORT_SERVER_URL/api/docs"` 로 확인할 수 있습니다.

```bash
curl -sS -X POST "$REPORT_SERVER_URL/api/docs" \
  -H "X-API-Key: $REPORT_API_KEY" \
  -F "file=@path/to/report.html" \
  -F "name=<영문-소문자-하이픈-이름>"
```

- 409가 반환되면 이미 같은 이름이 있다는 뜻입니다. 같은 보고서를 갱신하는 것이면 `curl -sS -X PUT "$REPORT_SERVER_URL/api/docs/<이름>" -H "X-API-Key: $REPORT_API_KEY" -F "file=@path/to/report.html"` 로 교체하고, 다른 보고서라면 날짜나 버전을 붙인 다른 `name` 으로 업로드하세요.
- 보고서는 단일 HTML 파일이어야 합니다. CSS/JS는 인라인으로 넣거나 CDN에서 불러오세요.
- 보고서는 sandbox에서 실행되므로 localStorage, 쿠키를 사용하지 마세요.
````

## 보안 설계

**업로드된 HTML 격리.** 보고서 응답에는 다음 헤더가 붙습니다.

```text
Content-Security-Policy: sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox allow-modals allow-downloads
X-Content-Type-Options: nosniff
```

`allow-same-origin` 을 주지 않았으므로 보고서는 고유한 opaque origin(`null`)에서 실행됩니다. 실제 Chromium으로 확인한 동작은 다음과 같습니다.

| 동작 | 결과 |
|---|---|
| 인라인 CSS/JS, SVG, CDN에서 불러온 Chart.js | 정상 동작 |
| 링크 새 창 열기, `alert()`, 파일 다운로드 | 정상 동작 |
| `fetch('/index')` 등 이 서비스의 다른 페이지 읽기 | 차단 (CORS) |
| 쿠키, `localStorage`, `sessionStorage` | 차단 (SecurityError) |
| Service Worker 등록 (다른 보고서 가로채기) | 차단 |
| `<form>` 제출 | 차단 (`allow-forms` 미부여) |

`window.print()` 같은 대화상자는 `allow-modals` 로 허용됩니다. 보고서가 localStorage를 사용하면(예: 다크 모드 토글 저장) 해당 기능은 예외가 발생합니다. 격리를 우선한 의도된 동작입니다. 목록 페이지는 스크립트를 쓰지 않으며 `default-src 'none'` 기반의 엄격한 CSP를 적용합니다.

**경로 조작 방지.** 업로드 이름과 조회 경로에 같은 검증 규칙을 적용합니다.

- `/`, `\`, `..`, 제어 문자, 양방향 텍스트 제어 문자(`U+202E` 등), Windows 예약 문자(`: * ? " < > |`)를 거부합니다.
- `.` 으로 시작하거나 끝나는 이름, 앞뒤 공백, 200바이트(UTF-8)를 넘는 이름을 거부합니다.
- 유니코드는 NFC로 정규화합니다. macOS가 보내는 분해형(NFD) 한글 파일명도 브라우저 URL과 일치합니다.
- 보고서 디렉터리 바로 아래의 일반 파일만 제공하며 심볼릭 링크는 따라가지 않습니다.

**인증.** 업로드·교체·삭제는 `X-API-Key` 헤더가 `UPLOAD_API_KEY` 와 일치할 때만 허용되며, 상수 시간 비교(`secrets.compare_digest`)를 사용합니다. 키가 설정되지 않으면 이 API들은 `503` 으로 비활성화됩니다. 조회와 목록(`/index`, `GET /api/docs`)은 인증 없이 공개됩니다. 교체·삭제도 조회와 같은 이름 검증을 거치므로 보고서 디렉터리 밖의 파일이나 심볼릭 링크는 건드릴 수 없습니다.

**URL 생성과 Host 헤더.** `PUBLIC_BASE_URL` 이 설정되어 있으면 항상 그 값을 사용합니다. 설정되지 않은 개발 환경에서는 Host 헤더가 `ALLOWED_HOSTS` 에 있을 때만 그대로 사용하고, 그 외에는 `http://localhost:{PORT}` 로 대체하고 경고 로그를 남깁니다. 위조된 Host 헤더가 응답 URL에 반영되지 않습니다.

**업로드 크기 제한.** 세 단계로 막습니다. (1) `Content-Length` 가 한도를 넘으면 본문을 읽기 전에 `413` 을 반환합니다. (2) `Content-Length` 가 없는 chunked 요청은 수신 바이트를 세다가 한도를 넘으면 중단합니다. (3) 저장 시 실제 파일 크기를 다시 확인합니다. FastAPI는 의존성(API 키 검사)보다 본문 파싱을 먼저 하므로 인증되지 않은 요청도 한도 크기까지는 수신될 수 있습니다. 1·2단계가 그 양을 제한합니다.

**원자적 저장.** 같은 디렉터리의 숨김 임시 파일(`.upload-*.tmp`)에 쓰고 `fsync` 한 뒤, 하드 링크(`os.link`)로 최종 이름을 붙입니다. `os.link` 는 기존 파일을 덮어쓰지 않으므로 동시에 같은 이름을 업로드해도 하나만 성공합니다(나머지는 409). 실패하면 임시 파일을 지우므로 불완전한 보고서가 남지 않습니다. 하드 링크를 지원하지 않는 파일시스템에서는 `O_EXCL` 생성과 `os.replace` 로 대체합니다. 교체(`PUT`)는 같은 방식으로 임시 파일을 완성한 뒤 `os.replace` 로 기존 파일과 한 번에 바꿉니다. 프로세스가 강제 종료되어 남은 1시간 이상 된 임시 파일은 다음 시작 시 정리합니다.

## 프로젝트 구조

```text
html-report-server/
├── app/
│   ├── __init__.py
│   ├── __main__.py          # python -m app: HOST/PORT 환경변수로 uvicorn 실행 (Docker CMD)
│   ├── main.py              # 앱 팩토리(create_app), 라이프사이클, 미들웨어·라우터 등록
│   ├── config.py            # 환경변수/.env 설정 (Settings)
│   ├── schemas.py           # Pydantic DTO (UploadResponse, ErrorResponse)
│   ├── dependencies.py      # 라우터 공용 의존성 (settings, storage)
│   ├── middleware.py        # 요청 본문 크기 제한 ASGI 미들웨어
│   ├── routers/
│   │   ├── upload.py        # /api/docs: 목록(GET, 공개), 업로드·교체·삭제(POST·PUT·DELETE, 인증)
│   │   └── documents.py     # GET /docs/{name}, GET /index, GET /
│   ├── services/
│   │   └── file_service.py  # 이름 검증, 원자적 저장·교체, 삭제, 디렉터리 스캔
│   ├── templates/
│   │   └── index.html       # 목록 페이지 (Jinja2, 자동 이스케이프)
│   └── static/
│       └── style.css        # 반응형 스타일 (라이트/다크 모드)
├── data/
│   └── reports/             # 보고서 저장 위치 (git 제외)
├── tests/                   # pytest
├── .env.example
├── requirements.txt         # 런타임 의존성
├── requirements-dev.txt     # 테스트 의존성 (pytest, httpx2)
├── pytest.ini
├── Dockerfile
├── .dockerignore
├── .gitignore
└── README.md
```

책임은 Router(HTTP 처리), Service(파일시스템 로직, HTTP를 모름), Schema(DTO)로 나눴습니다.

## 테스트

```bash
pip install -r requirements-dev.txt
pytest
```

테스트는 매번 임시 디렉터리를 보고서 저장소로 사용하므로 `./data/reports` 를 건드리지 않습니다. 검증 항목:

- 정상 업로드와 `201` 응답, DTO 필드와 절대 URL, `+09:00` 시간대
- 업로드 후 디스크에 원본 바이트 그대로 저장
- `/docs/{name}` 조회, `Content-Type: text/html; charset=utf-8`, 없는 보고서 `404`
- `.html` 이외 확장자 차단, 빈 파일·UTF-8이 아닌 파일 차단
- 중복 업로드 `409` 와 기존 파일 보존
- 교체(`PUT`): 내용 교체와 같은 URL 유지, 업로드 날짜 갱신·목록 순서, 없는 보고서 `404`, 거부(키·형식·크기·인코딩)되면 원본 보존, 경로 조작·심볼릭 링크 차단
- 삭제(`DELETE`): `204` 와 목록·조회에서 제거, 삭제 후 같은 이름 재업로드, 없는 보고서 `404`, 키 검사, 경로 조작·심볼릭 링크 차단
- 목록 JSON(`GET /api/docs`): 필드·최신순·`/index` 와 같은 순서, 대상 파일 필터, 한글 URL 인코딩, 인증 없이 공개, Host 헤더 위조 방지, 업로드·교체·삭제 반영
- Path Traversal 차단 (업로드 파일명, `name` 필드, 인코딩된 조회 경로, 심볼릭 링크)
- API 키 누락·불일치 `401`, 키 미설정 시 `503`
- `/index` 의 이름(확장자 제외)·날짜(Asia/Seoul)·최신순 정렬·HTML 이스케이프·빈 목록 메시지
- 크기 제한 (`Content-Length`, chunked 전송, 정확히 한도인 파일)
- 한글·공백 이름의 URL 인코딩, NFD → NFC 정규화
- 위조된 Host 헤더가 URL에 반영되지 않음
- 앱을 새로 띄워도(재시작) 기존 보고서 조회·목록 표시, 시작 시 디렉터리 자동 생성
- CSP sandbox 헤더 (`allow-scripts` 포함, `allow-same-origin` 미포함)

## 운영 참고

- **교체·삭제**: `PUT`/`DELETE /api/docs/{name}` 을 쓰거나, `data/reports/` 의 파일을 직접 지우거나 바꿔도 목록에 바로 반영됩니다. 이름 변경 API는 없으므로 파일 이름을 직접 바꾸세요.
- **직접 배치**: 이름 규칙에 맞는 `*.html` 파일을 디렉터리에 복사해도 목록과 조회에 나타납니다. 이때 업로드 날짜는 파일 mtime입니다.
- **백업**: `data/reports/` 디렉터리만 복사하면 됩니다.
- **리버스 프록시(HTTPS)**: `PUBLIC_BASE_URL=https://your.domain` 을 설정하세요. nginx 사용 시 `client_max_body_size` 를 `MAX_UPLOAD_SIZE_MB` 보다 크게 잡아야 합니다.
- **로그**: 업로드 성공·거부(사유, 클라이언트 IP), 인증 실패, 저장 오류를 표준 출력으로 남깁니다. 시작할 때 API 키 미설정, 예시 키(`change-me`) 사용, 저장 디렉터리 쓰기 불가를 경고합니다.
