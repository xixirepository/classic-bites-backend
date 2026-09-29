# iOS 로그인·회원가입 UI를 백엔드에 연결하는 프롬프트

UI 작업을 마친 뒤 아래 구분선 다음 내용을 iOS 저장소의 새 작업 요청에 그대로 붙여 넣습니다. 실제 연결 주소와 Google 클라이언트 ID가 준비되어 있으면 함께 제공합니다. 준비되지 않아도 API 클라이언트·화면 연결·모의 테스트를 먼저 구현하고 실제 연동만 미검증으로 남길 수 있습니다. 비밀번호, `.env`, Google client secret, 서버 관리자 키는 붙여 넣지 않습니다.

---

고전한입 iOS 앱에 완성된 로그인·회원가입 UI를 기존 FastAPI 백엔드에 연결해 주세요. 이메일·비밀번호 회원가입/로그인과 Google 로그인/첫 가입을 모두 연결하고 기존 화면 디자인을 유지해 주세요.

## 작업 위치와 범위

- iOS 저장소: `/Users/sean/xcode/classic-bites-ios`
- 백엔드 저장소: `/Users/sean/xcode/classic-bites-backend`
- API 계약: `/Users/sean/xcode/classic-bites-backend/docs/auth-api.md`
- 백엔드 활성화·검증 기록: `/Users/sean/xcode/classic-bites-backend/README.md`

먼저 iOS의 상위 지침·`AGENTS.md`·`README.md`, 하위 지침과 최신 UI 구현을 읽고 로그인·회원가입·로그인 후 화면, 기존 상태 관리, 네트워크 코드, 패키지 구성을 확인해 주세요. 과거의 로고 시작 화면만 있다고 가정하지 말고 현재 파일을 기준으로 작업해 주세요. 진행 중인 UI 작업과 기존 변경을 보존하고, 병행 작업이 있다면 지침에 따라 별도 worktree를 사용해 주세요. 현재 로컬 `dev`에서 작업 브랜치를 만들되 이미 같은 연결 작업 브랜치가 있으면 이어서 작업하세요. 로컬 `dev`가 없으면 출발점을 확인하기 전 브랜치를 임의로 만들지 마세요.

이번 변경 대상은 iOS 인증 연결입니다. 백엔드 소스와 API 계약은 읽고 사용하되 백엔드 수정·배포, 새로운 화면 디자인, 학습 기능, 추가 로그인 제공자, 회원 탈퇴·비밀번호 재설정 기능을 함께 추가하지 마세요. 현재 UI에 비밀번호 재설정 같은 미구현 기능의 버튼이 있다면 성공한 것처럼 꾸미지 말고 API가 아직 없음을 명확히 처리해 주세요.

## 연결 전 설정 확인

실제 API 기본 주소, iOS OAuth client ID, Web application OAuth client ID의 준비 상태를 확인해 주세요. 프로젝트에 안전하게 관리되는 기존 설정이 있으면 사용하고, 없으면 환경별 설정 지점과 자리표시자를 만들어 주세요. 실제 값이 없는 상태에서 성공했다고 보고하지 마세요.

API 기본 주소의 공개 운영값은 아직 미정입니다. 기존 `http://vpn.xixiplay.com:8000` 주소로 실제 비밀번호나 토큰을 전송하지 마세요. 공개 연결은 HTTPS를 사용하세요. 개발용 계정으로 Mac의 loopback SSH 터널을 쓰는 Simulator 검증은 가능하지만 실기기의 `127.0.0.1`은 Mac이 아닙니다. 모든 HTTP 요청을 허용하는 ATS 예외를 추가하지 말고, 로컬 개발에 필요한 설정은 Debug와 해당 연결에만 제한하세요.

Google Cloud 프로젝트·클라이언트 ID가 없다면 `docs/auth-api.md`의 설정 절차를 안내하고, 구현을 진행할 수 있는 부분은 계속해 주세요. Google client secret, `.env`, DB 비밀번호, `MEDIA_API_KEY`는 요구하거나 앱에 넣지 마세요.

## 반드시 사용할 API 계약

요청 본문은 JSON이고 `/api`·`/v1` 접두사는 없습니다.

| 동작 | 요청 | 성공 |
| --- | --- | --- |
| 이메일 가입 | `POST /auth/signup` → `{email,password,display_name}` | `201`, TokenResponse |
| 이메일 로그인 | `POST /auth/login` → `{email,password}` | `200`, TokenResponse |
| Google 로그인·가입 | `POST /auth/google` → `{id_token}` | `200`, TokenResponse |
| 세션 갱신 | `POST /auth/refresh` → `{refresh_token}` | `200`, TokenResponse |
| 로그아웃 | `POST /auth/logout` → `{refresh_token}` | `204`, 본문 없음 |
| 현재 사용자 | `GET /auth/me`, `Authorization: Bearer <access_token>` | `200`, User 객체 자체 |

TokenResponse 필드:

```text
access_token: String
refresh_token: String
token_type: "bearer"
expires_in: Int (초, 기본 900)
refresh_expires_in: Int (초, 세션의 남은 최대 수명)
user: {id, email, display_name, provider, email_verified, created_at}
is_new_user: Bool
```

User의 `id`는 UUID 문자열, `provider`는 `password` 또는 `google`, `email_verified`는 Bool, `created_at`은 UTC ISO 8601입니다. 나머지 DTO의 세부 규칙은 최신 API 문서·소스·OpenAPI로 확인해 정확히 맞춰 주세요. 네이밍 변환과 날짜 디코딩을 실제 응답 예시로 검증하세요.

Google 로그인 버튼과 Google 회원가입 버튼은 같은 Google SDK 인증 → 같은 `/auth/google` 요청을 사용합니다. `is_new_user=true`는 가입 완료, `false`는 기존 회원 로그인입니다. 화면에 기존 가입 완료 경로가 있으면 이 값으로 연결하되 새로운 온보딩을 임의로 만들지 마세요. Google 사용자 ID나 access token 대신 **Google ID token**을 보내세요. 반환된 고전한입 access token으로 이후 API를 호출하세요.

가입 시 `password`는 12–128자이고 로그인 입력은 1–128자이며, 입력을 trim하거나 정규화하지 않습니다. `display_name`은 trim 후 1–80자입니다. 비밀번호 확인은 UI에서 검사하고 서버에는 `password`만 보냅니다. 같은 이메일의 로그인 방식을 앱에서 자동 연결하거나 서버의 `409`를 무시하지 마세요. 이메일 가입의 `email_verified=false`를 이메일 인증 완료로 표시하지 마세요.

## iOS 구현 요구

프로젝트의 기존 구조에 맞춰 API 호출 계층과 인증 상태를 분리하고 Swift 동시성을 안전하게 사용해 주세요. 버튼 중복 탭을 막고 진행 상태를 표시하며 성공·실패·취소·통신 오류의 모든 경로에서 로딩을 해제하세요. 기존 UI의 색·레이아웃·문구·내비게이션은 필요한 인증 상태 표현만 최소 수정하세요.

access/refresh token은 JWT가 아닌 opaque 문자열입니다. 내용을 디코딩해 권한·만료를 판단하지 마세요. 토큰 쌍과 필요한 만료 정보를 하나의 Keychain 항목으로 저장하고, 갱신 성공 시 함께 교체하세요. 비밀번호나 토큰을 UserDefaults·로그·분석 이벤트·URL에 남기지 마세요. 로그인 성공 응답을 받아도 토큰 저장에 실패하면 로그인 완료로 표시하지 마세요.

앱 시작 시 보관된 세션으로 `/auth/me`를 확인해 인증 상태를 복구하세요. 오프라인을 잘못된 비밀번호로 표시하거나 서버 검증 없이 로그인 성공을 새로 만들어 내지 마세요. 보호 요청의 401은 refresh 후 원래 요청을 최대 한 번 재시도하고 무한 반복을 막으세요.

refresh token은 한 번만 사용합니다. 갱신하면 기존 access/refresh token 모두 무효화되고 새 쌍이 발급됩니다. 사용한 refresh token 재사용은 그 세션에서 파생된 토큰까지 해제합니다. 동시에 여러 요청이 실패하더라도 앱 전체에 refresh 요청은 하나만 실행하고 다른 요청은 그 결과를 기다리게 하세요. 최초 세션의 최대 수명은 기본 30일이며 갱신으로 연장되지 않습니다. refresh 응답 유실·시간 초과로 처리 여부가 불명확하면 같은 token을 자동 재전송하지 말고 세션을 지운 뒤 재로그인을 요청하세요.

로그아웃은 현재 refresh token을 서버에 보내 세션을 해제하고 Keychain·메모리 상태를 지웁니다. 네트워크 오류가 나도 로컬 로그아웃은 완료하고 서버 해제 미확인 사실을 안내하세요. 갱신 중 로그아웃이나 계정 전환이 일어나면 늦게 도착한 응답으로 이전 세션이 부활하지 않도록 처리하세요. Google SDK의 로컬 로그인 상태도 정리하되 로그아웃만으로 Google 계정 연결 권한을 철회하는 동작을 추가하지 마세요.

Google SDK가 이미 있으면 활용하고 없으면 공식 Google Sign-In iOS 패키지를 앱 타깃에 연결하세요. 현재 공식 문서와 프로젝트 호환성을 확인해 버전을 결정하고 의존성 잠금 파일을 보존하세요. `GIDClientID`는 iOS 유형 ID, `GIDServerClientID`는 Web application 유형 ID, URL scheme은 역순 iOS client ID입니다. 앱으로 돌아오는 URL 처리를 기존 SwiftUI 진입점과 충돌 없이 연결하세요. backend `GOOGLE_CLIENT_IDS`에도 같은 Web application ID가 허용되어 있어야 합니다. ID token은 Google SDK에서 필요 시 갱신해 획득하세요. iPhone·iPad 모두 적절한 화면에서 인증창을 표시하고, 사용자가 창을 취소하면 원래 화면에 머물게 하세요. [Google iOS 설정](https://developers.google.com/identity/sign-in/ios/start-integrating), [Google iOS 로그인](https://developers.google.com/identity/sign-in/ios/sign-in), [백엔드 ID token 전달](https://developers.google.com/identity/sign-in/ios/backend-auth)

오류는 `{detail:{code,message,fields?}}`이며 선택적 `fields`는 `{field,type}` 객체 배열입니다. 상태 코드와 `code`를 기준으로 한국어 메시지와 재시도 여부를 정하세요. `401 invalid_credentials`, `invalid_token`, `invalid_google_token`; `408 request_timeout`; `409 email_in_use`; `413 request_too_large`; `422 validation_error`; `429 rate_limited`와 `Retry-After`; `503 google_not_configured`, `google_unavailable`, `auth_unavailable`, `auth_disabled`를 처리하세요. 알 수 없는 응답·디코딩 오류도 사용자에게 복구 가능한 실패로 표시하세요. 토큰 갱신을 회원가입·로그인 실패의 자동 해결책으로 호출하지 마세요.

## 검증과 완료 보고

현재 프로젝트에서 가능한 빌드·테스트를 실행하세요. 기존 테스트 기반이 있으면 활용하고, 필요한 경우 네트워크 모의를 통해 다음 핵심 상태 전이를 검증할 최소 테스트를 추가하세요: 가입/로그인 성공, 오류 DTO 처리, 동시 401의 단일 refresh, 회전 후 토큰 교체, refresh 응답 유실, 로그아웃 중 늦은 응답, 앱 재시작 세션 복구. 테스트를 통과시키기 위해 실제 인증 검증을 우회하지 마세요.

사용자 확인 항목으로 정상 가입, 중복 이메일, 잘못된 비밀번호, 입력 길이 검증, Google 첫 가입·재로그인·취소·이메일 충돌, 만료 후 갱신, 로그아웃, 오프라인과 503, iPhone·iPad 화면 동작을 제시하세요. Google 설정이 없으면 모의 검증과 실제 Google 연동 미검증을 구분하세요. 실제 HTTPS API 주소가 없다면 운영 연결을 완료했다고 하지 마세요.

완료 보고에는 저장소·worktree 경로, 브랜치, Git 초기화 여부, 수정 파일과 역할, 실행한 검증과 실패·미실행 이유, 사용자 확인 항목, 실제 연결에 남은 설정, 생성 커밋 ID 또는 미커밋 사실, push 미실행을 포함하세요. 명시적인 사용자 테스트 통과 확인 전에는 통합을 위한 rebase·squash·`dev` 병합을 하지 마세요. push와 브랜치·worktree 삭제도 수행하지 마세요.
