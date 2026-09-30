# 로그인·회원가입 API와 Google 설정

이 문서는 이메일·비밀번호 회원가입/로그인, Google 계정 첫 로그인 시 가입, 세션 갱신과 로그아웃의 계약입니다. iOS 화면 연결용 요청문은 [iOS 인증 연결 프롬프트](ios-auth-integration-prompt.md)에 있습니다.

2026-09-30 인증 API `1.2.0`을 서버에 배포하고 HTTPS·인증 활성화와 native iOS audience 허용 목록을 적용했습니다. 사용자가 실제 iPhone에서 Google 로그인과 서재 진입 성공을 확인했습니다. 현재 주소와 서버 검증 결과는 [README의 인증 배포 기록](../README.md#11-로그인회원가입과-https-배포)을 확인하며, 초기 파일 API 설치 기록·모의 테스트·실제 회원 로그인을 구분합니다.

## 1. 지원하는 동작

| 화면 동작 | 백엔드 동작 |
| --- | --- |
| 이메일 회원가입 | 회원 생성 후 즉시 고전한입 세션 발급 |
| 이메일 로그인 | 이메일·비밀번호 검증 후 세션 발급 |
| Google 로그인 / Google 회원가입 | 같은 `/auth/google` 호출; 처음 보는 Google 계정이면 가입, 기존 계정이면 로그인 |
| 로그인 상태 복구 | 보관한 access token으로 `/auth/me` 확인; 필요할 때 한 번만 세션 갱신 |
| 로그아웃 | 해당 refresh token이 속한 세션 해제 |

비밀번호는 Argon2id 해시로 저장합니다. 세션은 예측하기 어려운 opaque token이며 JWT가 아닙니다. DB에는 access/refresh token 원문 대신 해시를 저장합니다. 앱은 토큰 내용을 해석하지 않고 응답의 수명과 서버 응답을 사용합니다.

이메일 인증 메일 발송, 비밀번호 재설정, 계정 연결·해제, 회원 탈퇴, 전체 기기 로그아웃은 이번 API 범위에 없습니다. 이메일 회원의 `email_verified`는 `false`입니다. 이메일 주소 일치만으로 별도 로그인 방식의 계정을 자동 연결하지 않습니다.

## 2. 요청·응답 계약

모든 POST 요청은 `Content-Type: application/json`을 사용합니다. 경로에는 `/api`나 `/v1` 접두사가 없습니다. 공개 앱용 기본 주소는 `https://vpn.xixiplay.com`입니다.

| 메서드 / 경로 | 요청 | 성공 응답 |
| --- | --- | --- |
| `POST /auth/signup` | `email`, `password`, `display_name` | `201`, TokenResponse; `is_new_user=true` |
| `POST /auth/login` | `email`, `password` | `200`, TokenResponse; `is_new_user=false` |
| `POST /auth/google` | `id_token` | `200`, TokenResponse; 최초 가입만 `is_new_user=true` |
| `POST /auth/refresh` | `refresh_token` | `200`, TokenResponse; 새 access/refresh token 모두 반환 |
| `POST /auth/logout` | `refresh_token` | `204`, 응답 본문 없음 |
| `GET /auth/me` | `Authorization: Bearer <access_token>` | `200`, 아래 `user` 객체 자체 |

`signup`의 `display_name`은 앞뒤 공백 제거 후 1–80자, `password`는 12–128자입니다. 로그인은 1–128자의 비밀번호 입력을 받아 실제 자격증명을 검사합니다. 비밀번호는 앱에서 앞뒤 공백 제거·대소문자 변환·유니코드 정규화를 하지 않고 입력 그대로 전송합니다. 비밀번호 확인 필드가 화면에 있으면 앱에서 일치 여부를 확인하며 서버에는 한 번만 보냅니다. 이메일은 서버에서 앞뒤 공백 제거·형식 검사·정규화·소문자 변환하여 비교합니다.

요청 예시는 모두 가상 데이터입니다.

```json
{
  "email": "reader@example.com",
  "password": "Example-only-long-passphrase",
  "display_name": "고전 독자"
}
```

TokenResponse 예시:

```json
{
  "access_token": "<opaque-access-token>",
  "refresh_token": "<opaque-refresh-token>",
  "token_type": "bearer",
  "expires_in": 900,
  "refresh_expires_in": 2592000,
  "user": {
    "id": "b18f4b84-87b0-4aa1-a8b7-52f24b30e0d9",
    "email": "reader@example.com",
    "display_name": "고전 독자",
    "provider": "password",
    "email_verified": false,
    "created_at": "2026-09-29T00:00:00Z"
  },
  "is_new_user": true
}
```

`id`는 UUID 문자열, `provider`는 `password` 또는 `google`입니다. `created_at`은 UTC의 ISO 8601 시각입니다. 수명 필드는 초 단위이며 응답값을 기준으로 처리합니다. 기본 access token 수명은 900초이고 세션의 최대 수명은 최초 발급부터 2,592,000초(30일)입니다. 갱신을 반복해도 최초 세션의 만료일이 뒤로 밀리지 않으므로 `refresh_expires_in`은 줄어듭니다.

Google 요청에는 SDK가 받은 **ID token** 문자열을 `id_token`으로 보냅니다. Google access token, Google의 사용자 ID 문자열, 앱에서 만든 이메일·이름 객체로 대체할 수 없습니다.

```json
{
  "id_token": "<google-id-token>"
}
```

### 오류 처리

인증 API는 아래 형식으로 반환합니다. 입력 오류에서 선택적으로 제공하는 `fields`는 `[{"field":"body.email","type":"value_error"}]` 형태이며 비밀번호나 토큰 원문을 포함하지 않습니다. 앱은 문장 비교 대신 HTTP 상태와 `code`로 동작을 결정하고 사용자에게 한국어로 안내합니다.

```json
{
  "detail": {
    "code": "invalid_credentials",
    "message": "<설명>"
  }
}
```

| HTTP / code | 의미와 앱의 처리 |
| --- | --- |
| `401 invalid_credentials` | 이메일 로그인 실패. 이메일·비밀번호 확인 안내 |
| `401 invalid_token` | 세션이 만료·해제되었거나 유효하지 않음. `/auth/me` 등에서 발생하면 갱신을 최대 한 번 시도하고, refresh 자체가 실패하면 토큰을 지우고 재로그인 |
| `401 invalid_google_token` | Google ID token 검증 실패. Google 로그인부터 다시 수행 |
| `409 email_in_use` | 이미 사용 중인 이메일. 기존 로그인 방식으로 로그인하도록 안내; 자동으로 계정을 합치거나 덮어쓰지 않음 |
| `408 request_timeout` | 서버가 요청 본문을 받는 시간이 초과됨. 연결 상태 확인 |
| `413 request_too_large` | 인증 요청 본문이 16 KiB를 초과함. 잘못된 요청 구성을 확인 |
| `422 validation_error` | 필수값·형식·길이 오류. 해당 입력을 수정하도록 안내 |
| `429 rate_limited` | 요청 제한. `Retry-After` 초 동안 연속 호출을 멈추고 안내 |
| `503 google_not_configured` | 서버의 Google 클라이언트 설정 누락. 이메일 로그인은 별도로 사용 가능 |
| `503 google_unavailable` | Google 검증 서비스 연결 문제. 잠시 후 다시 시도하도록 안내 |
| `503 auth_unavailable` | 인증 저장소 등 서버 문제. 성공 화면으로 이동하지 않고 일시 장애 안내 |
| `503 auth_disabled` | 서버에서 인증 기능을 아직 활성화하지 않음. 설정 확인 필요 |

통신 단절·시간 초과·예상하지 못한 오류와 Google 창의 사용자 취소도 구분합니다. 취소는 일반 로그인 실패 경고로 처리하지 않고 원래 화면에 머무르게 합니다.

## 3. 세션 갱신·보관

refresh token은 한 번만 사용할 수 있습니다. 정상 갱신 시 기존 access token과 refresh token은 모두 효력을 잃고 새 쌍이 발급됩니다. 사용했던 refresh token을 다시 제출하면 해당 세션에서 파생된 토큰을 해제합니다. 따라서 앱은 다음 조건을 지켜야 합니다.

1. 토큰 쌍은 Keychain에 하나의 묶음으로 보관하고 갱신 응답을 받은 뒤 함께 교체합니다. 비밀번호를 보관하지 않습니다.
2. 앱 전체에서 진행 중인 refresh 요청은 하나만 유지합니다. 동시에 여러 요청이 401을 받아도 같은 갱신 결과를 기다립니다.
3. 갱신한 access token으로 원래 요청을 최대 한 번만 재시도합니다. refresh 실패를 다시 refresh하는 반복 호출은 금지합니다.
4. refresh 요청 후 응답을 받기 전에 연결이 끊기면 서버 처리 여부를 알 수 없습니다. 같은 refresh token을 자동으로 재전송하지 말고 로컬 세션을 비운 뒤 다시 로그인하도록 안내합니다.
5. 로그아웃은 현재 refresh token으로 요청합니다. 이미 해제되었거나 알 수 없는 형식상 유효한 token의 로그아웃은 `204`로 끝나는 멱등 동작입니다. 일반 입력 형식 오류는 `422`일 수 있습니다.
6. 로그아웃 통신 실패 시에도 앱의 토큰과 사용자 상태를 지웁니다. 서버 해제가 확인되지 않았다는 점을 사용자에게 알리고, Google SDK의 로컬 로그인 상태도 정리합니다.

`MEDIA_API_KEY`는 기존 관리자용 파일 API의 공유 키입니다. 회원 access token과 다르며 앱 번들·설정·Keychain에 넣을 키가 아닙니다. 이번 로그인으로 기존 `/files` API를 일반 회원에게 공개하지 않습니다.

## 4. Google Cloud가 없는 경우 설정 순서

2026-09-30 확인한 Google 공식 문서와 Google Sign-In iOS SDK `9.2.0`의 설정 계약을 기준으로 작성했습니다. Google 콘솔의 메뉴 표기는 바뀔 수 있습니다. 아래 작업은 계정 소유자가 자신의 Google 계정으로 진행하며 비밀번호나 클라이언트 시크릿을 채팅으로 전달할 필요가 없습니다.

1. [Google Cloud 콘솔](https://console.cloud.google.com/)에서 프로젝트 선택 → 새 프로젝트를 열고 고전한입용 프로젝트를 만듭니다. 생성된 프로젝트를 선택합니다. [Google 프로젝트 생성 안내](https://developers.google.com/workspace/guides/create-project)
2. **Google Auth Platform → Branding**에서 시작하기를 누르고 앱 이름, 지원 이메일, 연락 이메일을 설정합니다. 일반 사용자 서비스에 맞춰 Audience를 External로 구성하고 개발 중에는 테스트 사용자를 등록합니다. Data Access에는 로그인에 필요한 `openid`, 이메일, 기본 프로필만 사용하며 Drive·Gmail 접근 권한을 추가하지 않습니다. 공개 전에는 실제 서비스 정보와 콘솔에서 요구하는 검증을 완료합니다. [Google 동의 화면 설정 안내](https://developers.google.com/workspace/guides/configure-oauth-consent)
3. **Clients → Create client → iOS**를 선택합니다. Xcode의 **ClassicBites 앱 타깃**에서 현재 Bundle Identifier를 확인해 입력합니다. 프레임워크 타깃의 식별자를 사용하지 않습니다. 콘솔이 추가 앱 정보를 요구하면 실제 배포 설정에서 확인하고 추측하지 않습니다. 생성된 iOS client ID와 표시되는 iOS URL scheme을 기록합니다.
4. 권장 구성은 같은 프로젝트에서 **Web application** 유형 클라이언트를 하나 더 만들고 이 ID를 백엔드용 audience로 사용하는 것입니다. iOS 클라이언트만 사용하는 native 구성도 아래 조건으로 지원합니다. 이 구현은 iOS SDK에서 받은 ID token을 검증하므로 백엔드 OAuth callback URL을 새로 만들지 않습니다. 웹 클라이언트의 비밀키를 iOS 앱에 넣지 않습니다. [Google 클라이언트 생성 안내](https://support.google.com/cloud/answer/15549257)
5. 아래 표에 맞춰 iOS와 백엔드 설정을 연결합니다. iOS에서는 Google Sign-In 공식 패키지를 연결하고, 로그인 후 앱으로 돌아오는 URL을 SDK에 전달해야 합니다. 구체적인 코드와 패키지 버전은 UI 연결 시 현재 프로젝트와 공식 문서를 확인해 정합니다. [Google iOS 설정 안내](https://developers.google.com/identity/sign-in/ios/start-integrating), [iOS 로그인 연결 안내](https://developers.google.com/identity/sign-in/ios/sign-in)

| 설정 위치 | Web application audience 구성 (권장) | iOS client ID만 사용하는 native 구성 |
| --- | --- | --- |
| iOS `GIDClientID` | iOS 유형 OAuth client ID | iOS 유형 OAuth client ID |
| iOS `GIDServerClientID` | Web application 유형 OAuth client ID | 생략; SDK 설정에는 `nil` 사용 |
| iOS URL Types → URL Schemes | iOS client ID의 점 구분 순서를 뒤집은 값; Google 콘솔의 iOS URL scheme과 일치 | 동일 |
| 서버 `.env`의 `GOOGLE_CLIENT_IDS` | 위 **Web application** ID | 위 **iOS** client ID |

Google Sign-In iOS SDK `9.2.0`의 `serverClientID`는 선택값입니다. 서버 ID 없이 iOS client ID로 설정할 수 있으며, 공식 iOS 예제도 이 설정으로 ID token을 받습니다. 앱에서 서버 ID 누락만으로 Google 인증창을 막지 않습니다. 빈 설정값은 `nil`로 처리하고, 서버 ID를 명시한다면 실제 Web application ID를 사용합니다. iOS ID를 `GIDServerClientID`에 복사하는 방식으로 대체하지 않습니다. [SDK 설정 계약](https://github.com/google/GoogleSignIn-iOS/blob/9.2.0/GoogleSignIn/Sources/Public/GoogleSignIn/GIDConfiguration.h), [Google의 iOS client ID 설정·ID token 예제](https://firebase.google.com/docs/auth/ios/google-signin#implement_google_sign-in)

이 백엔드의 `GoogleVerifier`는 클라이언트 유형을 Web application으로 제한하지 않고 ID token의 `aud`가 `GOOGLE_CLIENT_IDS` 허용 목록에 있는지 검사합니다. 서버 ID를 생략한 native 구성에서는 iOS ID를, 서버 ID를 지정한 구성에서는 Web application ID를 허용해야 합니다. 여러 앱·환경을 지원한다면 의도적으로 허용할 ID만 쉼표로 구분합니다. 허용 목록 누락·audience 불일치를 해결하기 위해 서명·issuer·만료·audience 검증을 끄지 않습니다. [Google의 audience 검증 안내](https://developers.google.com/identity/sign-in/ios/backend-auth#verify-the-integrity-of-the-id-token)

백엔드는 Google의 서명·issuer·만료·허용 audience를 확인하고 `sub`를 Google 계정의 식별자로 사용합니다. ID token 검증에는 Google client secret이 필요하지 않습니다. 요청받은 Google 계정이 처음이라면 검증된 이메일 claim이 있어야 가입됩니다. 이미 아는 `sub`는 저장된 회원으로 로그인하며 초기 이메일을 자동 변경하지 않습니다. Gmail 또는 Google Workspace에서 이메일 소유권을 확인할 수 있는 계정만 `email_verified=true`로 저장하고, 외부 이메일을 쓰는 Google 계정은 `false`로 유지합니다. 이는 Google 로그인 성공 여부와 별개입니다. [Google 백엔드 인증 안내](https://developers.google.com/identity/sign-in/ios/backend-auth)

Google에서 승인된 후에도 서비스 내부 이메일이 다른 계정에 이미 있으면 `409 email_in_use`입니다. 사용자에게 기존 로그인 방식을 안내하며 이메일만 보고 기존 계정을 연결하지 않습니다.

## 5. 서버 활성화와 연결 주소

인증 기능은 기본적으로 꺼져 있습니다. 새 설치 예시와 초기화 도구는 `AUTH_ENABLED=false`를 준비하며, 기존 `.env`에 이 키가 없을 때에도 `./stack.sh install`은 `false`로 추가합니다. 이미 있는 값은 유지하고 프로세스·Compose에서 환경 변수가 누락되어도 비활성화합니다. 실제 `.env`를 저장소·문서·채팅에 복사하지 않습니다.

아래는 최초 설치용 절차입니다. 기존 정상 서비스에 API만 적용할 때는 [README의 FastAPI 전용 HTTPS 적용 절차](../README.md#기존-nginx를-통한-https-적용)를 사용합니다. 현재 서버에는 HTTPS와 인증을 적용했으며 이후 갱신에도 기존 `.env`와 볼륨을 보존합니다.

```sh
./stack.sh install
./stack.sh status
./stack.sh check
```

`install`은 기존 자격증명을 유지하면서 누락된 설정과 rate-limit salt를 준비하고, 이미지를 빌드한 뒤 MySQL·MinIO가 준비되면 인증 테이블을 적용하고 FastAPI를 시작합니다. 인증 기능이 꺼져 있어도 테이블은 준비하며, 설치만으로 인증을 활성화하지 않습니다.

HTTPS 연결 또는 개발용 loopback SSH 터널을 준비한 뒤, 대상 서버의 비공개 `.env`에서 `AUTH_ENABLED=true`를 명시적으로 설정하고 다음으로 적용합니다. 공개 운영에서는 기존 HTTP 포트로 인증 요청이 직접 들어오지 않도록 접근 경로도 제한하고 HTTPS 경로를 사용합니다. 개발용 터널만 쓰는 환경도 인증 포트의 공개 접근을 제한해야 합니다. `AUTH_ENABLED` 자체가 전송 구간을 암호화하거나 직접 HTTP 접근을 차단하지는 않습니다.

```sh
docker compose config --quiet
./stack.sh start
./stack.sh status
```

Google ID가 없으면 이메일 인증을 활성화해 검증할 수 있지만 Google 로그인은 `503 google_not_configured`로 응답합니다. 실제 회원은 HTTPS 연결을 준비한 후 받습니다.

이미지가 빌드되어 있고 DB가 실행 중인 환경에서 인증 스키마만 명시적으로 준비하는 명령은 다음과 같습니다.

```sh
docker compose run --rm --no-deps fastapi python migrate_auth.py
```

`api/migrations/001_auth.sql`은 `auth_users`, `auth_sessions`, `auth_refresh_tokens`, `auth_rate_limits` 테이블을 추가합니다. 기존 회원·미디어 테이블을 지우는 작업은 없으며 반복 적용 후 필요한 컬럼을 확인합니다. 인증이 켜진 상태의 `/ready`는 인증 스키마도 점검합니다. 기존 운영 데이터와 볼륨을 보존하며 최신 검증 결과는 [README](../README.md)를 확인합니다.

| 환경 변수 | 설정 |
| --- | --- |
| `AUTH_ENABLED` | 설치 기본값 `false`; 보호된 연결 준비 후 운영자가 명시적으로 `true`; 누락 시에도 비활성 |
| `AUTH_ACCESS_TTL_SECONDS` | 기본 `900` |
| `AUTH_REFRESH_TTL_SECONDS` | 기본 `2592000`; 최초 세션부터의 절대 최대 수명 |
| `AUTH_RATE_LIMIT_SALT` | 설치 과정에서 생성하는 비밀 난수; 예시값을 실제 환경에 쓰지 않음 |
| `GOOGLE_CLIENT_IDS` | 앱의 ID token audience와 일치하는 허용 목록: 서버 ID 지정 시 Web application ID, 서버 ID 생략 시 iOS ID; 비어 있으면 Google 인증은 설정 오류로 응답 |

이메일 로그인과 회원가입도 비밀번호·세션 토큰을 전송하므로 공개 인터넷에서는 현재 HTTPS 주소를 사용합니다. 초기 설치 기록의 공개 HTTP 주소는 실제 회원 인증용 기본 주소가 아닙니다. 개발 중에는 개발용 계정과 Mac의 loopback에 묶인 SSH 터널을 통한 iOS Simulator 연결을 사용할 수 있습니다. 실기기의 `127.0.0.1`은 Mac이 아니므로 같은 주소로 연결되지 않습니다. 실기기는 `https://vpn.xixiplay.com`으로 연결해 검증합니다.

iOS에서 전체 HTTP를 허용하는 `NSAllowsArbitraryLoads` 설정을 추가해 우회하지 않습니다. 필요한 개발 환경 설정은 Debug 범위와 해당 로컬 연결로 제한하고 실제 동작을 확인합니다. 인증 토큰을 URL 쿼리·로그·분석 이벤트에 남기지 않습니다.

## 6. UI 연결 후 사용자 확인 항목

- 이메일 신규 가입 → 로그인 후 화면 이동 → 앱 재실행 후 `/auth/me`로 사용자 복구.
- 중복 이메일, 잘못된 비밀번호, 잘못된 이메일 형식, 12자 미만 비밀번호, 빈 이름 오류 표시.
- access token 만료 후 갱신 성공, 동시 요청에서도 refresh 한 번만 실행, 새 토큰 쌍의 Keychain 교체.
- 만료·해제된 refresh token, 사용한 refresh token 재사용, 갱신 응답 유실 시 안전한 재로그인.
- 로그아웃 후 `/auth/me` 거부, 앱 재실행 후 로그인 화면, 로그아웃 통신 실패 안내.
- Google 첫 가입과 재로그인, `is_new_user` 분기, 기존 이메일 충돌, Google 창 취소, 선택한 구성의 audience 허용과 다른 client ID·만료된 ID token 거부. native 구성에서는 Web ID 없이 인증창이 열리고 서버가 iOS audience를 검증하는지 확인.
- 오프라인·타임아웃·429·503에서 로딩 해제와 재시도 안내; 실패를 로그인 성공으로 표시하지 않음.
- iPhone·iPad에서 Google 인증창 표시와 앱 복귀, 기존 UI·접근성·키보드 동작 유지.

실제 Google 연동 검증에는 클라이언트 ID와 Google 테스트 계정이 필요합니다. 모의 Google 검증 테스트가 통과해도 Google 콘솔 설정·사용자 동의·실제 iOS 인증창 동작까지 확인한 것은 아닙니다. 사용자 테스트 통과 확인 전에는 rebase·squash·`dev` 병합을 하지 않으며 push는 사용자가 직접 수행합니다.
