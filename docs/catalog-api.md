# 서재·콘텐츠 API 계약

이 API는 iOS와 별도 admin 저장소가 함께 사용하는 백엔드 계약이다. `catalog_` MySQL 테이블에 책·내부 작품·장·한입을 저장하며, 표지는 기존 비공개 MinIO 버킷에 저장한다. 서비스의 사용자 인증은 [기존 인증 API](auth-api.md)를 재사용한다. 학습 진도·결제·사용자용 웹·Android는 포함하지 않는다.

## 계층과 공개 정책

```text
서재
└── 책 (사서오경, 관리자가 추가한 손자병법 등)
    ├── 내부 작품 (대학, 중용 등)
    │   └── 장 → 한입 → 원문·번역·해설
    └── 직접 속한 장 → 한입 → 원문·번역·해설
```

내부 작품 없는 책은 직접 속한 장을 사용한다. 책 상세의 `chapters`에는 직속 장만 포함하고 작품 안의 장은 해당 작품 상세에서 조회한다. 장은 반드시 하나의 책에 속하며, `work_id`가 있으면 같은 책의 작품이어야 한다. 부모 ID는 생성 후 변경할 수 없다. 이번 구현은 추가·수정·정렬·초안·공개 취소를 제공하며 삭제와 부모 이동은 제공하지 않는다.

각 계층의 `is_published`는 독립된 공개 설정이다. 게스트 조회는 자신과 모든 상위 항목이 공개된 경우에만 허용한다. 비공개·공개 취소·존재하지 않는 ID는 모두 `404 content_not_found`다. 상위 항목을 비공개로 바꿔도 하위 항목의 저장된 공개 설정을 덮어쓰지 않으며, 상위를 다시 공개하면 하위의 원래 설정이 적용된다. 관리자는 초안과 비공개 콘텐츠도 조회·수정할 수 있다.

`is_ready`는 클라이언트가 수정할 수 없는 서버 계산값이다. 자신과 모든 상위 항목이 공개된 상태에서, 원문·번역·해설 중 하나 이상에 읽을 텍스트가 있는 공개 한입의 존재를 뜻한다. 공개된 책·작품·장에 준비된 한입이 없어도 탐색할 수 있다. 텍스트가 없는 공개 한입은 목차에 `is_ready=false`로 표시되며 본문 조회는 `409 content_preparing`을 반환한다. 빈 문자열·공백만으로 준비 완료가 되지 않도록 입력의 앞뒤 공백을 제거한다.

목록은 항상 `sort_order` 오름차순, 같은 순서에서는 `id` 오름차순이다. 제목 변경과 관계없이 서버가 발급한 UUID를 사용한다. 조회 응답과 오류, 표지 응답에는 `Cache-Control: no-store`를 적용한다. 각 상세 응답은 MySQL의 동일한 읽기 스냅샷을 사용한다. 공개 취소 이후 시작한 새 조회는 변경된 공개 상태를 확인하며, 이미 화면에 받은 본문을 원격으로 지우는 실시간 푸시 기능은 없다.

## 공통 응답 필드

| 모델 | 필드 |
| --- | --- |
| 공통 | `id: UUID 문자열`, `title: 문자열`, `sort_order: 정수`, `is_ready: 불리언` |
| Book | 공통 + `description: 문자열`, `cover_url: 문자열 또는 null` |
| Work | Book 필드 + `book_id: UUID 문자열`, `title_hanzi`, `title_pinyin`: 각각 문자열 |
| Chapter | 공통 + `book_id: UUID 문자열`, `work_id: UUID 문자열 또는 null`, `title_hanzi`, `title_pinyin`: 각각 문자열 |
| BiteSummary | 공통 + `chapter_id: UUID 문자열` |
| BiteDetail | BiteSummary + `original`, `pinyin`, `translation`, `commentary`: 각각 문자열 |

공개 응답에는 `is_published`와 MinIO `cover_key`를 포함하지 않는다. 관리자 응답에는 `is_published`가 추가되고 Book·Work에는 내부 관리용 `cover_key`도 포함한다. 한입 목록은 관리자에서도 본문을 제외한 요약이며 편집 시 한입 상세를 조회한다.

관리자 Work에는 `source_edition`, `source_url`, `pinyin_source`, `review_status`도 포함하며 관리자 책 상세의 works 목록에도 동일하게 제공한다. 공개 API에서는 이 네 관리 필드를 제외한다. 한자·병음 제목과 병음은 아직 등록하지 않았으면 빈 문자열이며 null은 사용하지 않는다.

`cover_url`은 API 서버 기준 절대 경로다. 예: `/catalog/books/<book-id>/cover`. iOS와 별도 admin 호스트는 **API 서버 origin**으로 해석해야 한다. 임의 외부 URL이나 MinIO 주소를 노출하지 않는다. 표지가 없으면 null이고, 저장소에서 파일이 사라졌거나 읽기가 실패하면 표지 요청이 실패한다. 클라이언트는 책 탐색을 유지하며 기본 표지를 표시한다.

## 공개 조회

인증 없이 사용할 수 있다. 요청 본문이 없다.

| 메서드·경로 | 성공 응답 |
| --- | --- |
| `GET /catalog/books` | `{ "items": [Book] }` |
| `GET /catalog/books/{id}` | `{ "book": Book, "works": [Work], "chapters": [Chapter] }` |
| `GET /catalog/works/{id}` | `{ "work": Work, "chapters": [Chapter] }` |
| `GET /catalog/works/{id}/reader` | `{ "work": Work, "chapters": [{ "chapter": Chapter, "bites": [BiteDetail] }] }` |
| `GET /catalog/chapters/{id}` | `{ "chapter": Chapter, "bites": [BiteSummary] }` |
| `GET /catalog/bites/{id}` | `{ "bite": BiteDetail }` |
| `GET /catalog/books/{id}/cover` | PNG·JPEG·WebP 바이트 |
| `GET /catalog/works/{id}/cover` | PNG·JPEG·WebP 바이트 |

### 한자·병음 reader

reader는 작품 선택 후 장마다 본문을 따로 요청할 필요 없이 공개 장·한입을 한 응답으로 제공한다. 작품·장·한입의 모든 상위 공개 상태와 동일한 DB 읽기 스냅샷을 사용하며, 장과 각 장의 한입은 각각 `sort_order`, `id` 순서다. 작품 이름에 따른 하드코딩은 없다.

reader 안의 `is_ready`만 원문 기준이다. `original.strip()`이 비어 있지 않은 공개 한입을 준비 완료로 판단하며 작품·장은 그런 하위 한입의 존재로 계산한다. 병음·번역·해설만 있는 한입은 false이고 빈 장·빈 작품도 HTTP 200으로 준비 상태를 반환한다. 원문이 있는데 병음이 비어 있으면 원문을 표시하고 병음 준비 상태를 안내할 수 있다. 기존 일반 조회의 원문·번역·해설 기준 `is_ready`와 개별 한입의 `409 content_preparing` 계약은 변경하지 않는다.

앱의 학습 콘텐츠는 작품·장의 `title_hanzi`/`title_pinyin`, 한입별 `original`/`pinyin`으로 표시한다. 기존 한국어 `title`·`translation`·`commentary`는 응답과 DB에 유지하며 한자·병음의 대체 본문으로 사용하지 않는다. 병음은 중국어 보통화 성조 부호를 포함한 유니코드 문자열을 그대로 저장한다. 자동 변환·발음 검수 기능은 없고 관리자가 검수된 읽기 단위의 원문과 병음을 짝으로 등록한다.

예시: 공개한 사서오경의 본문이 아직 없을 때도 목록에는 다음과 같이 나타난다. 이 UUID는 설명용 자리표시자이며 실제로는 서버 값을 사용한다.

```json
{
  "items": [{
    "id": "<server-issued-book-uuid>",
    "title": "사서오경",
    "description": "",
    "sort_order": 0,
    "is_ready": false,
    "cover_url": null
  }]
}
```

## 관리자 권한과 조회

`Authorization: Bearer <access_token>`에 기존 `/auth/login` 또는 `/auth/google`에서 발급한 access token을 보낸다. 서버는 매 요청마다 유효한 로그인 세션을 검사하고, 사용자의 안정적인 UUID가 `CATALOG_ADMIN_USER_IDS` 허용 목록에 있는지 확인한다. 이메일·표시 이름·클라이언트 플래그·`MEDIA_API_KEY`는 관리자 권한을 부여하지 않는다. 관리자 ID 목록은 서버의 비공개 환경 설정에서 관리하며 iOS에 포함하지 않는다.

`/admin/api`는 데이터 API이고 관리자 화면 소스는 별도 admin 저장소에 둔다. 별도 화면 호스트가 백엔드에 요청할 때 `CATALOG_ADMIN_ORIGINS`에 명시한 origin만 CORS 응답을 받을 수 있다. HTTPS origin과 로컬 개발용 `http://localhost:<port>`·`http://127.0.0.1:<port>`만 허용한다. 와일드카드, 자격증명 포함 주소, 경로·쿼리 포함 주소, 원격 HTTP는 거부한다. CORS는 관리자 인증을 대체하지 않는다. access token은 브라우저 메모리에 유지하고 HTML·URL·저장소에 기록하지 않는다.

| 메서드·경로 | 성공 응답 |
| --- | --- |
| `GET /admin/api/books` | `{ "items": [AdminBook] }` |
| `GET /admin/api/books/{id}` | `{ "book": AdminBook, "works": [AdminWork], "chapters": [AdminChapter] }` |
| `GET /admin/api/works/{id}` | `{ "work": AdminWork, "chapters": [AdminChapter] }` |
| `GET /admin/api/chapters/{id}` | `{ "chapter": AdminChapter, "bites": [AdminBiteSummary] }` |
| `GET /admin/api/bites/{id}` | `{ "bite": AdminBiteDetail }` |
| `GET /admin/api/books/{id}/cover` | 관리자 인증 후 표지 바이트 |
| `GET /admin/api/works/{id}/cover` | 관리자 인증 후 표지 바이트 |

관리 표지는 Bearer 헤더를 넣은 fetch로 받고 Blob URL로 표시한다. 관리 응답의 표지 경로를 인증 없는 `<img src>`에 직접 넣지 않는다.

## 생성·수정

`POST /admin/api/{books|works|chapters|bites}`로 생성한다. UUID는 서버가 발급하고 HTTP 201을 반환한다. `PUT /admin/api/{종류}/{id}`로 수정하고 HTTP 200을 반환한다. 성공 응답 형태는 같은 항목의 관리자 상세 GET과 동일하다. PUT은 아래 필드 전체를 보내는 방식이며, 생략 가능한 필드는 기본값으로 바뀐다. 공개 상태를 보존하려면 기존 값을 명시해야 한다.

| 대상 | JSON 필드 |
| --- | --- |
| 모든 대상 | `title` 필수, `sort_order` 기본 0, `is_published` 기본 false |
| books | `description` 기본 빈 문자열 |
| works | `book_id` 필수, `description`, `title_hanzi`, `title_pinyin`, `source_edition`, `source_url`, `pinyin_source` 기본 빈 문자열, `review_status` 기본 `draft` |
| chapters | `book_id` 필수, `work_id` 기본 null, `title_hanzi`, `title_pinyin` 기본 빈 문자열 |
| bites | `chapter_id` 필수, `original`, `pinyin`, `translation`, `commentary` 기본 빈 문자열 |

- 제목은 앞뒤 공백 제거 후 1~200자이며 줄바꿈·제어문자를 허용하지 않는다.
- 한자·병음 제목은 각각 0~200자이며 같은 공백·제어문자 규칙을 적용한다.
- 소개는 10,000자, 각 본문 필드는 100,000자 이하이고 앞뒤 공백을 제거한다.
- 원문 판본 `source_edition`은 1,000자, 출처 위치 `source_url`은 2,048자, 병음 출처·검수 근거 `pinyin_source`는 2,000자 이하이다. 서버가 출처를 열거나 자동 검증하지 않는다.
- `review_status`는 `draft` 또는 `reviewed`만 받는다. `reviewed`는 원문과 병음을 모두 검수한 종합 상태이며 공개 상태와 별도다. 확인되지 않은 자료는 비공개 `draft`로 저장하고 정식 검수 콘텐츠로 등록하지 않는다.
- `sort_order`는 -2,147,483,648~2,147,483,647의 정수다. 공개 상태는 JSON 불리언만 받는다.
- 모든 부모 ID는 UUID여야 한다. 다른 책의 작품에 장을 연결하거나 존재하지 않는 부모를 지정하면 `409 invalid_parent`다. 생성 후 부모를 바꾸면 `409 parent_immutable`다.
- 정의되지 않은 필드, `id`, `is_ready`, `cover_key`, `cover_url`을 일반 생성·수정 입력에 넣으면 422다. 표지는 별도 업로드 API로만 연결한다.
- JSON 쓰기 요청은 2 MiB·수신 15초로 제한한다.

한입 저장 예:

```json
{
  "chapter_id": "<existing-chapter-uuid>",
  "title": "관리자가 정한 한입 제목",
  "sort_order": 0,
  "is_published": false,
  "original": "",
  "pinyin": "",
  "translation": "",
  "commentary": ""
}
```

### 표지 업로드

`POST /admin/api/books/{id}/cover` 또는 `/admin/api/works/{id}/cover`에 multipart `file` 하나를 보낸다. 관리자 인증은 multipart 파싱 이전에 수행한다. 최대 5 MiB, PNG·JPEG·WebP 파일 시그니처만 허용하고 사용자 파일명이나 지정 경로를 저장 키로 사용하지 않는다. 이미지 전체 디코딩이나 리사이즈는 하지 않으므로 실제 표시 가능 여부는 클라이언트 이미지 디코더도 확인해야 한다.

성공하면 기존 비공개 미디어 버킷의 `images/catalog/<random-id>.<extension>`에 쓰고 해당 책·작품에 연결한다. 응답은 `{ "cover_url": "/admin/api/books/<id>/cover" }` 형태다. `X-Content-Type-Options: nosniff`와 제한된 CSP를 적용해 이미지로 제공한다. 여러 프로세스 간 전역 제한은 아니며 현재 API 프로세스당 업로드 하나, 요청 30초로 제한한다.

이전 표지 객체와 DB 연결 실패 시 남은 객체는 삭제하지 않는다. 동시 작업 중 참조 파일 삭제를 피하고 기존 파일을 보존하는 정책이며, 사용하지 않는 객체 정리는 별도 운영 작업이다. MySQL과 MinIO 사이 분산 트랜잭션은 없으므로 저장소 쓰기 이후 DB 실패 시 관리자에게 실패가 반환되고 객체만 남을 수 있다.

## 오류

일반 콘텐츠 오류는 `{ "detail": { "code": "...", "message": "한국어 안내" } }` 형식이다. 기존 미디어 저장소 오류는 기존 API와 같이 문자열 `detail`을 사용할 수 있으므로 클라이언트는 HTTP 상태를 기준으로 기본 안내도 처리한다.

| 상태·코드 | 의미 |
| --- | --- |
| 401 `invalid_token` | 토큰 누락·만료·폐기 |
| 403 `admin_required` | 정상 사용자지만 관리자 아님 |
| 404 `content_not_found` | 비공개 조상 포함 접근 불가 또는 없는 콘텐츠 |
| 404 `cover_not_found` / 미디어 404 | 표지 미등록 또는 객체 누락 |
| 409 `content_preparing` | 공개 한입이나 본문 텍스트가 없음 |
| 409 `invalid_parent`, `parent_immutable` | 부모 연결 불일치 또는 변경 시도 |
| 413 `request_too_large`, `cover_too_large` | JSON·표지 크기 상한 초과 |
| 415 `invalid_cover`, `invalid_upload` | 지원하지 않는 표지·업로드 형식 |
| 422 `validation_error` | UUID·필드·길이·형식 오류; 입력값은 오류에 반사하지 않음 |
| 429 `upload_busy` | 같은 프로세스에서 다른 표지 업로드 진행 중 |
| 503 `catalog_disabled` | 서재 기능 비활성 |
| 503 `auth_disabled` | 관리자 인증 서비스 비활성 |
| 503 기타 | DB·미디어 연결 실패 |

## 설치와 보존

`CATALOG_ENABLED` 기본값은 false다. 명시적으로 마이그레이션하고 활성화한다. 기존 `.env` 값과 DB·MinIO 볼륨을 유지한다. `CATALOG_ADMIN_USER_IDS`는 기존 회원 UUID의 쉼표 구분 목록이다. 첫 관리자 계정은 기존 인증으로 생성한 뒤 해당 UUID를 서버 설정에 등록한다. 자체 관리자 승격 API는 없다.

API 이미지에 새 소스가 반영되고 MySQL이 실행 중인 환경에서:

```sh
# 추가 테이블 네 개와 누락된 한자·병음·출처 컬럼을 준비한다.
docker compose run --rm --no-deps fastapi python migrate_catalog.py

# 선택: 사서오경 한 권과 대학·중용·논어·맹자·시경·서경·역경·예기·춘추 제목만 공개한다.
docker compose run --rm --no-deps fastapi python migrate_catalog.py --seed-classics
```

마이그레이션은 같은 데이터베이스의 동시 실행을 이름 잠금으로 직렬화하고 필수 컬럼을 검사한다. `002_catalog.sql`로 새 테이블의 기본키·외래키·정렬 인덱스를 준비하고 `003_hanzi_pinyin.sql`의 누락 컬럼을 추가한다. 기존 행의 새 문자열은 빈 값, 검수 상태는 `draft`가 되며 원문·번역·해설·제목·공개 상태는 보존한다. 적용 계정에 `ALTER` 권한이 필요하다. MySQL DDL은 전체 롤백되지 않으므로 중간 실패 시 원인을 고친 뒤 같은 명령을 재실행한다. 각 컬럼의 존재를 확인하므로 이미 추가된 컬럼은 다시 만들지 않는다. 기존 auth 테이블과 미디어 객체를 변경하지 않는다.

선택 seed는 고정된 UUID로 같은 항목을 반복 생성하지 않으며, 이미 등록한 항목의 제목·순서·공개 상태를 덮어쓰지 않는다. 본문·장·한입이나 임의 번역을 추가하지 않는다. 운영 백업·배포·환경 설정·관리자 지정은 별도 실제 적용 단계다. 로컬 테스트 통과를 운영 서버 적용으로 표현하지 않는다.

## 자동 검증

프로젝트의 Python 3.13 가상환경과 `api/requirements-test.txt`를 사용한다. 마지막 명령은 로컬에 준비한 `mysql:8.4` 이미지와 Docker가 필요하다. 기존 서버·환경 파일·볼륨을 사용하지 않고 고유 이름의 임시 컨테이너를 만든 뒤 제거한다.

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/check-catalog.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/check-catalog-media.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/check-catalog-mysql.py
```

2026-09-30 한자·병음 변경 로컬 검증: HTTP 계약·권한 11개, 표지·응답 보호·CORS 7개, 실제 MySQL 8.4.10 저장·HTTP 인증 흐름·공개 계층·외래키·롤백·정렬·마이그레이션/seed 반복 검증 14개 통과. 기존 스키마에서 추가 컬럼 업그레이드, 장문·성조 병음 재연결 보존, 원문 기준 reader 준비 상태, 동시 공개 취소 중 동일 스냅샷도 확인했다. 표지 테스트는 가짜 저장소를 사용하며 실제 MinIO 통합·관리자 브라우저·실기기 검증 결과는 README의 별도 기록을 따른다. 운영 서버는 `1.3.0`이며 이번 확장은 배포하지 않았다. Starlette의 httpx 변경 예고 경고는 기존 테스트 환경에서도 발생하며 실패는 아니다.
