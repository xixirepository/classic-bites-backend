# 고전한입 백엔드 · Classic Bites Backend
이 저장소는 고전한입 백엔드의 소스와 Docker 운영 구성을 관리합니다. iOS 앱은 별도 `classic-bites-ios`, 관리자 화면은 별도 [classic-bites-admin](https://github.com/xixirepository/classic-bites-admin) 저장소에서 관리합니다. 작업 규칙은 [AGENTS.md](AGENTS.md)를 따릅니다.

세 서비스를 Docker Compose 프로젝트 하나로 설치하고 함께 시작·중지하는 구성입니다. 서버의 기존 서비스와 구분되는 `classic-bites-stack` 프로젝트를 사용합니다. 2026-09-30 FastAPI는 기존 nginx를 통한 `https://vpn.xixiplay.com`으로 공개하며, 호스트의 API 포트는 `127.0.0.1:8000`에만 연결합니다. MySQL·MinIO의 기존 포트 설정은 유지합니다. 초기 설치 기록과 인증·HTTPS 배포 결과는 각각 9절과 11절에 있습니다. 2026-09-30 대학 등록 때 확인한 운영 API는 `1.4.0`입니다. 서재 구현·운영 배포와 대학 목차·본문 등록 결과는 12절에서 구분해 기록합니다.

## 1. 설치 위치와 구성

| 항목 | 값 |
| --- | --- |
| 서버 | `vpn.xixiplay.com` |
| SSH 계정 / 포트 | `xixi` / `2222` |
| 서버 설치 경로 | `/home/xixi/classic-bites-stack` (`~/classic-bites-stack`) |
| Compose 프로젝트 | `classic-bites-stack` |
| Compose 서비스 이름 | `fastapi`, `mysql`, `minio` |
| 데이터베이스 / 앱 계정 | `classic_bites` / `classic_bites` |
| MinIO 관리자 계정 | `classicbitesadmin` |
| MinIO 미디어 버킷 | `classic-bites-media` (비공개) |
| FastAPI 전용 MinIO 계정 | `classicbitesapi` |
| 파일 API 인증 | `X-API-Key` 헤더, 서버 `.env`의 `MEDIA_API_KEY` |

SSH 비밀번호와 서비스 비밀번호는 이 문서에 저장하지 않습니다. MySQL root 비밀번호, 앱 DB 비밀번호, MinIO 관리자 비밀번호는 설치 과정에서 각각 생성하여 서버의 `.env`에 저장합니다.

주요 파일은 다음과 같습니다.

| 경로 | 역할 |
| --- | --- |
| `compose.yaml` | 세 서비스, 내부 네트워크, 호스트 접속 포트, 데이터 볼륨 정의 |
| `compose.https.yaml` | 기존 nginx 연결, FastAPI loopback 포트와 신뢰 프록시 설정 |
| `deploy/nginx-classic-bites.conf` | API HTTPS·ACME와 스트림 프록시 템플릿 |
| `stack.sh` | 설치·시작·중지·상태 확인 명령 |
| `.env` | 서버에서 생성하는 실제 비밀번호와 환경 설정 |
| `.env.example` | 비밀번호를 포함하지 않는 설정 예시 |
| `api/` | FastAPI 앱, 인증·서재·콘텐츠 관리·표지 API와 Docker 빌드 파일 |
| `api/migrations/` | 기존 데이터를 보존하는 인증·서재 추가 스키마 |
| `docs/catalog-api.md` | iOS·독립 admin이 공유하는 콘텐츠 API 계약 |
| `minio/` | 고정된 MinIO 소스를 빌드하는 Docker 파일 |
| `scripts/` | 설치 보조 및 연결 점검 도구 |
| `README.md` | 이 운영 설명서 |

2026-09-30 대학 등록 때 확인한 배포 서버의 API 버전은 `1.4.0`이며 `/health`, `/ready`, `/docs`, 미디어 파일 CRUD와 로그인·회원가입 API, 책·작품·장·한입의 공개 조회와 콘텐츠 관리 API를 제공합니다. 인증은 활성화했고 사용자가 실제 iPhone에서 Google 로그인과 서재 진입 성공을 확인한 기록은 [인증 기능](#11-로그인회원가입과-https-배포)에 있습니다. 2026-09-30 서재 API를 운영에 배포·활성화하고 사서오경 한 권과 내부 작품 아홉 개의 제목을 공개했습니다. 같은 날 후속 요청으로 대학의 11개 장과 원문·한국어 번역·해설 34개 한입을 운영에 등록했습니다. 번역·해설은 전문 감수 전 학습용 초안이며, 표지와 다른 작품의 본문은 아직 없습니다. 관리자 계정·origin 지정과 관리자 화면 배포는 별도 항목입니다. 학습 기록 저장과 범용 MySQL 파일 정보 테이블도 없습니다. 파일 API는 아래 버킷의 다섯 경로만 사용하며 `X-API-Key` 인증이 필요합니다. FastAPI는 전용 MinIO 계정으로 이 버킷의 파일을 관리합니다. MinIO 관리자 비밀번호는 초기 권한 설정과 설치 점검 프로세스에 표준입력으로 일시 전달하며, FastAPI의 상시 환경 변수에는 넣지 않습니다.

### 미디어 버킷과 파일 경로

2026-09-29 `classic-bites-media` 버킷을 생성했습니다. 익명 접근 정책을 추가하지 않은 비공개 버킷이며, 인증된 계정의 권한에 따라 사용합니다.

```text
classic-bites-media
├── images/       이미지·썸네일
├── videos/       동영상
├── audio/        음성
├── documents/    PDF·학습 자료
└── subtitles/    자막
```

MinIO 콘솔에서 `classic-bites-media`를 열면 위 경로를 볼 수 있습니다. 빈 경로도 표시되도록 `/`로 끝나는 0바이트 객체를 만들었습니다. 실제 파일은 예를 들어 `images/cover.jpg`, `videos/lesson-01.mp4`라는 이름으로 저장합니다. 이는 파일 이름의 경로 구분이며, 실제 파일시스템 폴더와는 다릅니다.

인증된 경로 목록 조회와 표시용 객체 읽기를 확인했고, 익명 목록 조회와 `images/` 객체 읽기는 모두 HTTP 403으로 거부됨을 확인했습니다. 버킷과 경로는 MinIO 데이터 볼륨에 보존됩니다. `./stack.sh install`은 없는 버킷과 경로만 생성하고 기존 파일은 유지합니다. 기존 MinIO 데이터 백업을 복구하여 사용할 수도 있습니다.

## 2. 한 번에 설치하고 시작하기

먼저 사용자 컴퓨터의 터미널에서 서버에 접속합니다.

```sh
ssh -p 2222 xixi@vpn.xixiplay.com
```

서버에 접속한 뒤 실행합니다.

```sh
cd ~/classic-bites-stack
./stack.sh install
./stack.sh status
./stack.sh check
```

`install`은 `.env` 초기 준비, 이미지 빌드, MySQL·MinIO 시작, 미디어 버킷·전용 계정·권한 설정, 회원 인증·서재 테이블 준비, FastAPI 시작을 순서대로 처리합니다. `install`은 콘텐츠를 자동 등록하지 않으며 사서오경 제목 seed는 12절의 선택 명령으로만 실행합니다. 기존 `.env`의 비밀번호는 유지하고 새 설정만 추가합니다. 각 서비스의 정상 상태를 기다립니다. 처음 MinIO를 소스에서 빌드할 때는 시간이 걸립니다. `--wait-timeout 180`은 서비스 시작 후 대기 제한이며 전체 이미지 다운로드·빌드 시간 제한은 아닙니다.

대상 서버에는 Docker와 Docker Compose가 이미 설치되어 있습니다. `stack.sh install`은 이 세 서비스를 설치하는 명령이며 Docker 엔진 자체를 설치하거나 서버의 다른 Compose 프로젝트를 관리하는 명령은 아닙니다.

## 3. 시작·종료·재시작

아래 명령은 모두 서버의 `~/classic-bites-stack`에서 실행합니다.

| 할 일 | 명령 | 결과 |
| --- | --- | --- |
| 최초 설치 / 이미지 다시 빌드하여 적용 | `./stack.sh install` | 이미지 빌드 후 서비스 시작 |
| 전체 시작 | `./stack.sh start` | 세 서비스 시작, 정상 상태 대기 |
| 전체 중지 | `./stack.sh stop` | 컨테이너를 중지하고 데이터 유지 |
| 전체 재시작 | `./stack.sh restart` | 중지 후 시작, 정상 상태 대기 |
| 컨테이너와 프로젝트 네트워크 정리 | `./stack.sh down` | 데이터 볼륨을 보존한 채 컨테이너 제거 |
| 상태 확인 | `./stack.sh status` | 컨테이너 실행·건강 상태 표시 |
| 전체 로그 보기 | `./stack.sh logs` | 전체 서비스 로그 표시 |
| API 로그 보기 | `./stack.sh logs fastapi` | FastAPI 로그 표시 |
| DB 로그 보기 | `./stack.sh logs mysql` | MySQL 로그 표시 |
| 저장소 로그 보기 | `./stack.sh logs minio` | MinIO 로그 표시 |
| 서비스 연결 및 읽기·쓰기 점검 | `./stack.sh check` | 기반 서비스 점검과 인증된 파일 CRUD·입력 검증 |

로그 보기에서 `Ctrl+C`를 눌러도 서비스는 계속 실행됩니다. 중지하려면 `stop`을 실행하세요. `down` 이후에도 `start`로 컨테이너를 다시 만들어 기존 데이터를 사용합니다.

직접 Docker Compose를 사용할 때의 기본 명령은 다음과 같습니다.

```sh
# 초기 설치: 비밀번호·버킷·전용 계정 설정까지 포함
./stack.sh install

# 이미 초기화된 환경에서 이미지 재빌드
docker compose up -d --build --wait --wait-timeout 180

# 시작
docker compose up -d --wait --wait-timeout 180

# 중지
docker compose stop

# 재시작: 설정 변경도 반영하도록 중지 후 up 사용
docker compose stop
docker compose up -d --wait --wait-timeout 180

# 컨테이너 정리: 데이터 볼륨은 유지
docker compose down

# 상태와 최근 로그
docker compose ps -a
docker compose logs --tail 100 fastapi

# 비밀번호를 출력하지 않고 Compose 설정 형식 검사
docker compose config --quiet
```

세 서비스의 재시작 정책은 `unless-stopped`입니다. 정상 운영 중 서버나 Docker가 재시작되면 컨테이너가 다시 시작됩니다. 사용자가 명시적으로 중지한 컨테이너는 `start`로 다시 시작해야 합니다.

메모리 상한은 MySQL과 MinIO가 각각 `1536 MiB`, FastAPI가 `512 MiB`입니다. FastAPI 임시 파일 공간은 메모리 기반 `/tmp` `128 MiB`이며 파일 업로드는 한 번에 하나, 파일당 기본 `100 MiB`로 제한합니다. 초기 서버 운영을 위한 설정이며 실제 사용량이 커지면 서버 여유 자원과 함께 조정해야 합니다. 컨테이너 로그는 파일당 `10 MB`, 최대 3개로 순환합니다.

## 4. 내 컴퓨터에서 접속하기

### 공유기 포트 포워딩으로 직접 접속

2026-09-29에는 세 서비스의 직접 외부 접속을 준비했습니다. 2026-09-30부터 API는 기존 nginx의 HTTPS 경로로 접속하고 직접 공개 `:8000`은 사용하지 않습니다. MySQL·MinIO는 기존 설정을 유지하며, API의 HTTPS 접속에는 SSH 터널이 필요하지 않습니다.

| 서비스 | 직접 접속 주소 | 서버 호스트 포트 → 컨테이너 포트 |
| --- | --- | --- |
| FastAPI API 문서 | <https://vpn.xixiplay.com/docs> | 기존 nginx `443` → Docker 내부 API `8000` |
| FastAPI 실행 상태 | <https://vpn.xixiplay.com/health> | 동일 |
| FastAPI 의존 서비스 상태 | <https://vpn.xixiplay.com/ready> | 동일 |
| FastAPI 서버 내부 접속 | 외부 접속 불가; 서버의 `http://127.0.0.1:8000` | `127.0.0.1:8000` → `8000` |
| MinIO 웹 콘솔 | <http://vpn.xixiplay.com:9001> | `0.0.0.0:9001` → `9001` |
| MinIO S3 API | `http://vpn.xixiplay.com:9000` | `0.0.0.0:9000` → `9000` |
| MySQL | 호스트 `vpn.xixiplay.com`, 포트 `3307` | `0.0.0.0:3307` → `3306` |

MySQL 접속 프로그램에서는 데이터베이스 `classic_bites`, 사용자 `classic_bites`, `.env`의 `MYSQL_PASSWORD`를 사용합니다. MySQL은 브라우저 주소가 아니라 DB 접속 프로그램에서 연결합니다. MinIO 콘솔에서는 사용자 `classicbitesadmin`, `.env`의 `MINIO_ROOT_PASSWORD`를 사용합니다.

외부 HTTPS와 ACME에는 기존 nginx의 TCP `443`·`80` 경로를 사용합니다. `:8000` 직접 접속은 차단합니다. 아래 MySQL·MinIO 포워딩은 초기 설치 설정이며 서버 내부 주소 `192.168.0.100`은 2026-09-29 확인 기준입니다. 이번 HTTPS 작업에서 공유기 설정 자체는 변경하지 않았습니다.

| 외부 TCP 포트 | 대상 서버 | 내부 TCP 포트 |
| --- | --- | --- |
| `3307` | `192.168.0.100` | `3307` |
| `9000` | `192.168.0.100` | `9000` |
| `9001` | `192.168.0.100` | `9001` |

MinIO의 `MINIO_BROWSER_REDIRECT_URL`은 Compose에서 `http://vpn.xixiplay.com:${MINIO_CONSOLE_PORT:-9001}`로 설정합니다. 기본 설정에서는 S3 API 주소를 브라우저에서 열 때 외부 콘솔 주소 `http://vpn.xixiplay.com:9001`로 이동합니다. MinIO의 직접 웹 주소는 기존 HTTP 설정이며 이번 HTTPS 적용 대상은 FastAPI입니다.

### 선택 사항: SSH 터널로 접속

SSH 터널을 사용할 때는 **서버 안이 아니라 사용자 컴퓨터의 터미널**에서 다음 명령을 실행한 뒤 창을 열어 둡니다.

```sh
ssh -p 2222 -N \
  -o ExitOnForwardFailure=yes \
  -L 127.0.0.1:8000:127.0.0.1:8000 \
  -L 127.0.0.1:9000:127.0.0.1:9000 \
  -L 127.0.0.1:9001:127.0.0.1:9001 \
  -L 127.0.0.1:3307:127.0.0.1:3307 \
  xixi@vpn.xixiplay.com
```

비밀번호를 입력한 뒤 아무 메시지가 없어도 정상일 수 있습니다. `-N`은 명령 셸을 열지 않고 터널만 유지합니다. `Ctrl+C`로 터널을 닫으면 터널을 이용하던 연결이 종료되며, 서버의 컨테이너는 계속 실행됩니다.

| 서비스 | 터널 사용 시 내 컴퓨터의 접속 주소 |
| --- | --- |
| FastAPI API 문서 | <http://127.0.0.1:8000/docs> |
| MinIO 웹 콘솔 | <http://127.0.0.1:9001> |
| MinIO S3 API | `http://127.0.0.1:9000` |
| MySQL | 호스트 `127.0.0.1`, 포트 `3307` |

내 컴퓨터의 포트가 이미 사용 중이면 `-L`에서 첫 번째 `127.0.0.1:` 뒤의 로컬 포트만 바꾸면 됩니다. 예를 들어 `-L 127.0.0.1:18000:127.0.0.1:8000`으로 실행하면 API 문서 주소는 `http://127.0.0.1:18000/docs`입니다. 다른 서비스도 같은 방식으로 바꿀 수 있습니다. MinIO 콘솔은 터널의 콘솔 주소로 직접 접속할 수 있습니다. S3 API 주소를 브라우저에서 열면 터널 사용 여부와 관계없이 위의 외부 콘솔 주소로 이동합니다.

컨테이너끼리는 Compose 내부 네트워크에서 `mysql:3306`, `minio:9000`으로 통신합니다.

## 5. 비밀번호 확인과 설정 변경

서버에서 본인만 볼 수 있는 터미널을 열고 확인합니다.

```sh
cd ~/classic-bites-stack
nano .env
```

설치 디렉터리는 권한 `700`, `.env`는 `600`으로 관리합니다. `.env`를 채팅·문서·저장소에 복사하거나 화면 공유에 노출하지 마세요. Docker 관리 권한을 가진 사람은 컨테이너 환경 변수에 접근할 수 있으므로 이 권한은 신뢰하는 관리자에게만 부여해야 합니다.

`.env.example`은 예시이고 실제 계정 비밀번호는 `.env`에 있습니다. 운영 중 `.env`를 삭제하여 새로 생성하면 기존 데이터에 설정된 비밀번호와 달라질 수 있으므로 원본을 보존하세요.

**기존 MySQL의 비밀번호는 `.env` 수정만으로 바뀌지 않습니다.** 공식 MySQL 이미지의 초기화 변수는 데이터 디렉터리가 비어 있을 때 적용됩니다. 운영 중 비밀번호를 변경할 때는 MySQL 안의 계정 비밀번호 변경과 `.env`의 대응 값을 함께 반영한 뒤 FastAPI를 다시 만들어야 합니다. [MySQL 공식 이미지의 환경 변수 설명](https://hub.docker.com/_/mysql)

일반 설정 변경 후에는 다음으로 적용·확인합니다.

```sh
docker compose config --quiet
./stack.sh start
./stack.sh check
```

Python 코드·패키지·Docker 빌드 파일을 변경한 경우 이미지를 다시 빌드합니다.

```sh
./stack.sh install
./stack.sh check
```

### 파일 CRUD API 사용

Swagger 문서: <https://vpn.xixiplay.com/docs>

1. 서버에서 `nano ~/classic-bites-stack/.env`로 `MEDIA_API_KEY`를 확인합니다.
2. Swagger의 **Authorize**에 이 키를 입력합니다. MinIO 관리자 비밀번호와는 다른 키입니다.
3. `POST /files`에서 `category`와 `file`을 선택해 업로드합니다.
4. 응답의 `key`를 복사해 조회·다운로드·교체·삭제 요청에 사용합니다.

| 메서드 / 경로 | 입력 | 동작 |
| --- | --- | --- |
| `POST /files` | multipart `file`, `category` | 고유한 파일 키를 만들어 업로드, 201 |
| `GET /files` | 선택적 `category`, `limit`, `start_after` | 파일 목록과 다음 조회 위치 반환 |
| `GET /files/info` | 쿼리 `key` | 크기·파일 형식·수정 시각 등 조회 |
| `GET /files/download` | 쿼리 `key` | 파일을 첨부파일로 다운로드 |
| `PUT /files` | 쿼리 `key`, multipart `file` | 기존 파일 내용을 교체 |
| `DELETE /files` | 쿼리 `key` | 파일 삭제, 204 |

`category`는 `images`, `videos`, `audio`, `documents`, `subtitles` 중 하나입니다. 카테고리는 저장 경로 구분이며 파일 내용의 형식을 검사하는 기능은 아닙니다. 새 파일에는 UUID가 포함된 키를 부여하므로 같은 이름으로 업로드해도 기존 파일을 덮어쓰지 않습니다. 조회 목록에는 폴더 표시용 객체가 나오지 않습니다.

`GET /files`는 `items`와 `next_start_after`를 반환합니다. 다음 페이지는 반환된 `next_start_after`를 `start_after`로 전달합니다. 기본 개수는 100, 최대 1,000개입니다. 교체는 같은 키의 파일 내용을 바꾸며 동시 수정 시 마지막으로 저장된 내용이 남습니다. 없는 파일은 404이며, 경로 밖의 키나 폴더 표시용 키는 수정·삭제할 수 없습니다.

현재 다운로드는 전체 파일 다운로드이며 동영상 구간 요청(Range)이나 스트리밍 재생 API를 구현한 것은 아닙니다. API 서버가 내부 `minio:9000`으로 파일을 처리하므로 이 CRUD API는 MinIO 외부 9000 포트 연결 없이도 동작합니다.

명령줄 예시에서는 실제 키를 문서에 적지 말고 직접 입력합니다. 아래는 사용자 컴퓨터의 **Bash**에서 실행하는 예시입니다.

```bash
read -r -s -p 'MEDIA_API_KEY: ' MEDIA_API_KEY
printf '\n'

# 새 파일 업로드: sample.jpg를 실제 파일 경로로 변경
curl --fail-with-body \
  -H "X-API-Key: $MEDIA_API_KEY" \
  -F category=images -F file=@sample.jpg \
  https://vpn.xixiplay.com/files

# 이미지 목록
curl --fail-with-body \
  -H "X-API-Key: $MEDIA_API_KEY" \
  'https://vpn.xixiplay.com/files?category=images&limit=20'

# 업로드 응답의 key 값을 입력
read -r -p '파일 key: ' object_key

# 파일 정보
curl --fail-with-body -G \
  -H "X-API-Key: $MEDIA_API_KEY" \
  --data-urlencode "key=$object_key" \
  https://vpn.xixiplay.com/files/info

# 파일 다운로드
curl --fail-with-body -G \
  -H "X-API-Key: $MEDIA_API_KEY" \
  --data-urlencode "key=$object_key" \
  -o downloaded-file \
  https://vpn.xixiplay.com/files/download

# 파일 키를 URL에 안전하게 넣기
export object_key
encoded_key="$(python3 -c 'import os,urllib.parse; print(urllib.parse.quote(os.environ["object_key"], safe=""))')"

# 기존 파일 교체: replacement.jpg를 실제 파일 경로로 변경
curl --fail-with-body -X PUT \
  -H "X-API-Key: $MEDIA_API_KEY" \
  -F file=@replacement.jpg \
  "https://vpn.xixiplay.com/files?key=$encoded_key"

# 파일 삭제
curl --fail-with-body -X DELETE \
  -H "X-API-Key: $MEDIA_API_KEY" \
  "https://vpn.xixiplay.com/files?key=$encoded_key"
unset MEDIA_API_KEY object_key encoded_key
```

파일 API 키는 관리자·서버 간 작업용 공유 키입니다. 이 키를 앱 번들이나 웹 프런트엔드에 넣으면 안 됩니다. 사용자별 로그인·파일 소유권 검사는 별도 구현 대상입니다. 외부 API 호출은 `https://vpn.xixiplay.com`을 사용하고 서버 내부·보호된 SSH 터널에서만 loopback HTTP로 연결합니다.

기본 업로드 한도는 `MAX_UPLOAD_BYTES=104857600`(100 MiB)이며 초과 시 413, 동시 업로드 제한 시 429를 반환합니다. 한도를 늘릴 때는 `/tmp` 공간과 FastAPI 메모리 상한도 함께 검토해야 합니다. 파일 API는 버킷 생성·삭제나 다른 버킷 접근 권한이 없습니다.

## 6. 데이터가 저장되는 곳

| 데이터 | Docker 볼륨 | 컨테이너 경로 |
| --- | --- | --- |
| MySQL | `classic-bites-stack_mysql_data` | `/var/lib/mysql` |
| MinIO 객체 및 메타데이터 | `classic-bites-stack_minio_data` | `/data` |

컨테이너 중지·재생성·일반 `down`으로 이 볼륨의 데이터가 사라지지 않습니다. 볼륨 삭제 옵션과 볼륨 정리 명령은 데이터 삭제 작업이므로 일반 운영에 사용하지 마세요. Compose 프로젝트 이름을 변경하면 다른 이름의 볼륨을 사용하여 데이터가 없어 보일 수 있습니다. [Docker 볼륨 수명 주기](https://docs.docker.com/engine/storage/volumes/)

이 구성은 단일 서버용입니다. 서버·디스크 장애에 대비한 자동 백업, 다른 서버로의 복제, 고가용성은 아직 구성하지 않았습니다.

## 7. 수동 백업과 복구

다음은 운영을 위한 참고 절차입니다. 실제 데이터 복구 연습은 별도 확인이 필요합니다. 백업에는 비밀번호와 사용자 데이터가 들어가므로 접근 권한을 제한하고, 검증된 사본을 서버 밖의 안전한 저장소에도 보관하세요.

### 백업

아래 절차는 서비스가 정상 실행 중인 상태에서 시작합니다. FastAPI와 MinIO를 잠시 중지하므로 점검 시간에 실행하세요. MySQL에 별도 접속하는 프로그램이 있다면 그 프로그램의 쓰기 작업도 먼저 중지합니다. 명령은 서버의 Bash에서 실행합니다.

```bash
cd ~/classic-bites-stack
(
  set -eu
  (
  set -eu
  umask 077
  backup_stamp="$(date +%Y%m%d-%H%M%S)"
  backup_dir="$PWD/backups/$backup_stamp"
  mkdir -p "$backup_dir"
  chmod 700 "$PWD/backups" "$backup_dir"

  # 성공·실패 시 모두 서비스 시작을 시도합니다.
  trap './stack.sh start' EXIT
  docker compose stop fastapi minio

  # 앱 데이터베이스의 논리 백업. 계정·권한 전체를 담는 백업은 아닙니다.
  docker compose exec -T mysql sh -c \
    'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysqldump -uroot --single-transaction --routines --events --triggers --databases "$MYSQL_DATABASE"' \
    > "$backup_dir/mysql.sql"
  test -s "$backup_dir/mysql.sql"

  # 중지된 MinIO의 객체와 숨김 메타데이터를 함께 보관합니다.
  minio_container="$(docker compose ps -aq minio)"
  test -n "$minio_container"
  docker cp -a "$minio_container:/data/." - > "$backup_dir/minio.tar"
  tar -tf "$backup_dir/minio.tar" > /dev/null

  cp -p .env "$backup_dir/environment.env"
  tar -czf "$backup_dir/deployment.tar.gz" \
    compose.yaml compose.https.yaml stack.sh .env.example README.md api minio scripts deploy
  chmod 600 "$backup_dir"/*
  printf '백업 경로: %s\n' "$backup_dir"
  )
  ./stack.sh check
)
```

각 명령이 성공했는지 확인한 뒤 백업을 외부에 보관합니다. `mysql.sql`은 `classic_bites` 데이터베이스용이며, 이후 추가한 다른 데이터베이스나 MySQL 계정·권한은 별도로 백업해야 합니다. `minio.tar`는 같은 MinIO 버전으로 복구하는 전체 데이터 디렉터리 백업입니다. Docker의 파일 복사 기능은 중지된 컨테이너와 tar 스트림을 지원합니다. [Docker 파일 복사 설명](https://docs.docker.com/reference/cli/docker/container/cp/)

### 새 서버의 빈 환경에 복구

**아래 예시는 기존 운영 데이터가 없는 새 서버·새 Docker 환경에서만 실행합니다.** 기존 데이터 위로 가져오면 같은 이름의 테이블·객체를 덮어쓸 수 있습니다. 운영 서버의 볼륨을 삭제하거나 비우는 절차는 포함하지 않습니다.

1. 동일한 Docker 구성을 사용할 새 서버에 백업 파일을 전송합니다.
2. `~/classic-bites-stack`에 `deployment.tar.gz`를 풀고, `environment.env`를 `.env`라는 이름으로 복사합니다. 디렉터리 권한 `700`, `.env` 권한 `600`을 설정합니다.
3. 같은 이름의 기존 MySQL·MinIO 데이터 볼륨이 없는지 확인합니다. 존재한다면 아래 절차를 진행하기 전에 대상 환경과 보존할 데이터를 다시 확인합니다.
4. 아래의 `/안전한/백업/경로`를 실제 복사한 백업 디렉터리로 바꿔 실행합니다.

```bash
cd ~/classic-bites-stack
(
set -eu
backup_dir='/안전한/백업/경로'
docker compose config --quiet
docker compose build

# 컨테이너와 빈 볼륨만 만들고 MySQL부터 시작합니다.
docker compose create
docker compose up -d --wait --wait-timeout 180 mysql

docker compose exec -T mysql sh -c \
  'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot' \
  < "$backup_dir/mysql.sql"

# 아직 시작하지 않은 MinIO의 빈 데이터 볼륨에 원래 소유권을 보존하여 복구합니다.
minio_container="$(docker compose ps -aq minio)"
docker cp -a - "$minio_container:/data" < "$backup_dir/minio.tar"

./stack.sh start
./stack.sh check
)
```

명령 중 오류가 나면 다음 단계로 진행하지 말고 오류를 먼저 확인하세요. 복구 후 DB 내용, MinIO 버킷·객체 목록과 파일 다운로드를 직접 확인해야 복구가 완료됩니다. 백업 이후 변경한 MySQL 계정·권한이나 MinIO 버전이 있다면 해당 차이도 반영해야 합니다.

## 8. 상태 확인과 문제 해결

```sh
cd ~/classic-bites-stack
./stack.sh status
./stack.sh check
docker compose logs --tail 100 mysql minio fastapi
```

- `/health`는 FastAPI 프로세스가 응답하는지 확인합니다.
- `/ready`는 MySQL의 `SELECT 1`, MinIO의 `/minio/health/cluster`, 전용 S3 계정으로 미디어 버킷 접근을 확인합니다. `checks`에는 `mysql`, `minio`, `media_bucket` 결과가 있으며 인증 활성 상태에서는 `auth_schema`도 확인합니다.
- `./stack.sh check`는 기반 서비스의 임시 DB·S3 점검, 파일 CRUD HTTP 통합 검사, 업로드·연결 종료 처리 테스트 11개를 실행합니다. 검사는 직접 만든 파일만 정리합니다. 기반 점검 실패로 `.smoke-state.json`이 남으면 원인을 해결한 뒤 `python3 scripts/smoke.py cleanup`으로 해당 점검 데이터만 정리하고 다시 실행합니다. 파일 CRUD 검사에서 정리에 실패하면 남은 검사 파일 키를 출력합니다.
- `unhealthy`나 시작 대기 시간 초과가 나오면 해당 서비스 로그를 확인합니다. 첫 MySQL 초기화나 MinIO 빌드가 진행 중인지도 확인하세요.
- `Address already in use`가 SSH 터널에서 나오면 내 컴퓨터의 해당 포트를 확인하고 터널의 왼쪽 포트를 바꿉니다. 서버에서 나오면 다른 서비스의 포트를 바꾸지 말고 이 Compose의 충돌을 확인합니다.
- 직접 접속이 거부되거나 시간 초과가 나면 서버의 컨테이너와 포트 수신 상태, 공유기의 해당 TCP 포트 포워딩을 확인합니다. SSH 터널을 사용할 때는 터널도 유지되는지 확인합니다.
- MySQL 인증 오류가 나면 `.env`와 실제 DB 계정 비밀번호가 일치하는지 확인합니다. 데이터 볼륨을 삭제하여 해결하지 마세요.
- `docker compose config`의 일반 출력에는 환경 변수의 비밀번호가 포함될 수 있습니다. 공유용 점검에는 `docker compose config --quiet`를 사용하고, 로그에도 비밀번호가 포함되지 않았는지 확인한 뒤 전달하세요.

## 9. 버전 및 설치 검증 기록

설치·검증일: 2026-09-29. 당시 세 서비스 설치를 완료했으며 최종 상태는 모두 `healthy`였습니다. 아래 표는 실제 서버와 SSH 터널을 통한 초기 설치 기록이며 2026-09-30의 API 버전·공개 주소·포트 변경은 11절에 별도로 기록합니다.

| 구성 요소 | 선택 / 확인 내용 | 상태 |
| --- | --- | --- |
| Docker 엔진 | `29.5.2` | 서버에 기존 설치됨 |
| Docker Compose | `v5.1.4` | 서버에 기존 설치됨 |
| MySQL | 공식 이미지 `mysql:8.4.11` | 실행 버전 확인 완료 |
| Python | 공식 이미지 `python:3.13.15-slim-bookworm` | 실행 버전 확인 완료 |
| FastAPI / Uvicorn | `0.141.1` / `0.54.0` | 설치 버전 확인 및 `pip check` 통과 |
| FastAPI 앱 이미지 | `classic-bites-fastapi:1.1.0` | 미디어 파일 CRUD 구현 |
| 업로드 처리 의존성 | `python-multipart==0.0.32`, `starlette==1.7.0` | 고정 버전으로 빌드 |
| PyMySQL / cryptography / MinIO Python SDK | `1.2.3` / `50.0.1` / `7.2.20` | 설치 버전 확인 및 `pip check` 통과 |
| MinIO 빌드 도구 | `golang:1.27.1-bookworm` | 빌드 성공, 런타임 버전 확인 |
| MinIO | `RELEASE.2025-10-15T17-29-55Z` 소스 빌드 | 빌드 성공, 실행 버전·커밋 확인 |
| MinIO 고정 커밋 | `9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a` | Docker 빌드 파일과 일치 확인 |
| Compose 형식·실제 시작·서비스 연결 | `config --quiet`, `stack.sh install`, `stack.sh check` | 모두 통과 |
| 파일 CRUD 통합 검사 | 다섯 카테고리·기본 카테고리 업로드, 조회·정확한 다운로드, 교체·삭제, 페이지네이션, 폴더 보호, 인증·오류 처리 | 통과, 검사 파일 정리 완료 |
| 업로드·다운로드 자원 검사 | 실제 스트림 크기 제한, 잘린 요청, 연결 종료, 동시 업로드, 다운로드 연결 반환 | ASGI 테스트 11개 통과 |
| 외부 파일 API | 인증 없는 요청 401, 인증된 업로드 201·다운로드 200·삭제 204 | 외부 연결 통과 |
| 전용 MinIO 권한 | 다른 버킷 파일 읽기·버킷 정책 조회·허용 경로 밖 쓰기 거부 | 통과; API 환경에 관리자 자격증명 없음 |
| 계정 초기화 반복 실행 | `setup-media-access.py` 두 번 실행 | 기존 계정·경로 유지, 두 번 모두 통과 |
| FastAPI 외부 접속 | `http://vpn.xixiplay.com:8000/docs` Swagger UI 및 `/health`, `/ready` | 외부 HTTP 200 확인, MySQL·MinIO 연결 정상 |
| SSH 터널 연결 | API 문서·상태, MinIO 콘솔 HTTP 200, MySQL 8.4.11 연결 응답 | 최초 설치 시 통과; 사용자 브라우저 로그인은 확인 항목 |
| MySQL 외부 접속 | `vpn.xixiplay.com:3307` | 외부 계정 로그인·SELECT 1 통과; 서버 CA를 신뢰하는 TLS 연결 확인 |
| MinIO 콘솔 외부 접속 | `http://vpn.xixiplay.com:9001` | HTML HTTP 200, 로그인 HTTP 204와 인증 세션 HTTP 200 확인 |
| MinIO S3 API 외부 접속 | `http://vpn.xixiplay.com:9000/minio/health/cluster` 및 브라우저 이동 | 외부 연결 5초 시간 초과; 공유기 TCP 9000 포워딩 확인 필요. 서버 LAN에서는 HTTP 200 및 외부 콘솔 주소로 HTTP 307 이동 확인 |
| 데이터 보존 | DB 행·S3 객체 생성 → `stack.sh down` → `stack.sh start` → 동일 데이터 읽기 | 통과, 검사 데이터 정리 완료 |
| 장애 감지 | MinIO 중지 시 `/health` 200, `/ready` 503과 MinIO 오류 | 통과, 이후 정상 복구 |
| 포트·권한 | FastAPI `0.0.0.0:8000`; MySQL `0.0.0.0:3307`; MinIO `0.0.0.0:9000`, `0.0.0.0:9001`; 설치 폴더 700, `.env` 600 | 서버에서 공개 바인딩 적용 확인; MySQL·MinIO healthy 및 서비스 연결·읽기·쓰기 점검 통과 |
| 기존 서비스 | 기존 8개 컨테이너 ID·시작 시각·실행 상태 비교 | 모두 동일 |
| 서버 재부팅 | Docker 자동 시작 활성화, `unless-stopped` 확인 | 실제 재부팅 시험은 미실행 |
| 백업 복구 연습 | 이 문서의 참고 절차 | 미실행 |

MinIO 커뮤니티 공식 저장소는 2026-04-25에 보관 처리되었으며 유지보수 중단을 알리고 있습니다. 이 구성은 고정한 공식 소스 릴리스를 Docker 이미지로 빌드합니다. 해당 버전을 선택했다고 이후 보안 수정이나 운영 지원이 제공되는 것은 아닙니다. [MinIO 공식 저장소의 유지보수 안내](https://github.com/minio/minio), [고정한 공식 릴리스](https://github.com/minio/minio/releases/tag/RELEASE.2025-10-15T17-29-55Z)

운영 요구가 커질 때에는 유지보수 가능한 저장소 대안, 사용자별 인증·파일 소유권, 백업 주기·보존 기간·복구 시험, 서버 자원과 모니터링을 별도 결정해야 합니다. 이 구성은 iOS 저장소와 분리된 백엔드 전용 저장소에서 관리합니다. 위 표는 2026-09-29 서버 설치·CRUD 적용 시점의 검증 기록이며, 현재 API HTTPS 배포 결과와 구분합니다. 로컬 Git 이력 정리만을 위해 서버를 재배포하거나 재시작하지 않습니다.

사용자 확인 항목은 `/docs`에서 `MEDIA_API_KEY`로 Authorize한 뒤 테스트 파일을 업로드·조회·교체·삭제하고, MinIO 콘솔에서도 저장 경로를 확인하는 것입니다. MinIO 파일 API를 외부에서 직접 사용할 경우 TCP `9000` 포워딩은 별도로 확인해야 합니다. 업무 기능을 추가할 때는 원하는 API 동작, 데이터·파일 범위와 완료 조건을 알려 주세요.

## 10. 소스 관리와 로컬 검증

기본 문서 최초 커밋은 `main`에 두고 해당 지점에서 `dev`를 만듭니다. 구현 변경은 현재 로컬 `dev`에서 만든 `feature/minio-crud`와 같은 작업 브랜치에서 진행합니다. 사용자 통합 승인 후 현재 `dev`로 rebase하고 해당 작업의 커밋만 하나로 squash한 뒤 `git merge --no-ff`로 `dev`에 병합합니다. 원격 저장소 생성·연결과 push는 별도 요청 범위이며 push는 사용자가 직접 수행합니다.

실제 `.env`, 백업, 배포 압축파일과 Python 캐시는 버전 관리에서 제외합니다. 서버의 기존 `.env`와 Docker 데이터 볼륨은 서버에서 보존하며 로컬 소스 저장소로 복사하지 않습니다. 배포 설정의 호스트·포트는 현재 설치 서버에 맞춘 값입니다.

아래 로컬 검사는 실제 DB·MinIO 없이 가짜 저장소를 사용하는 ASGI 테스트 11개를 실행합니다. Python 3.13 환경에서 저장소 루트를 기준으로 실행하세요. 이 검사는 서버에서 실행하는 `./stack.sh check`의 전체 통합 검사를 대신하지 않습니다.

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install -r api/requirements.txt
.venv/bin/python -m pip check
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/check-media-limits.py

# 예시 환경으로 Compose 문법만 검증하며 서비스를 시작하지 않습니다.
docker compose --env-file .env.example config --quiet
bash -n stack.sh
git diff --check
git diff --cached --check
git status --short
```

다음 기능 요청에는 원하는 API 동작, 대상 데이터·파일 범위, 완료 조건과 필요하면 선호 브랜치 이름을 적어 주세요.

## 11. 로그인·회원가입과 HTTPS 배포

이메일·비밀번호 가입/로그인과 Google ID token 로그인/첫 가입을 추가했습니다. 로그인 유지, 현재 사용자 조회, access/refresh token 갱신과 로그아웃을 포함합니다. 2026-09-30 API `1.2.0`을 서버에 배포하고 HTTPS와 인증을 활성화했습니다. 위 9절의 `1.1.0`은 초기 설치 기록입니다. 사용자가 실제 iPhone에서 Google 계정 로그인과 서재 진입 성공을 확인했습니다. 에이전트는 계정·비밀번호·실제 ID token을 조회하지 않았습니다.

- [인증 API 계약과 Google Cloud 설정 안내](docs/auth-api.md): 요청·응답, 오류 코드, 서버 활성화, Google 클라이언트 생성 및 사용자 확인 항목.
- [UI 완료 후 사용할 iOS 연결 프롬프트](docs/ios-auth-integration-prompt.md): 완성된 화면을 유지하며 인증 API·Google SDK·Keychain·세션 갱신에 연결하는 작업 요청문.
- `api/auth.py`, `api/auth_guard.py`: 인증 API와 비밀번호 해시, Google 서명 검증, 요청 제한·오류 보호.
- `api/auth_store.py`, `api/migrations/001_auth.sql`: MySQL 회원·세션·갱신 이력·시도 제한 저장과 트랜잭션.
- `api/migrate_auth.py`, `api/prune_auth.py`: 인증 테이블 준비와 만료된 인증 기록의 제한된 정리.
- [compose.https.yaml](compose.https.yaml), [nginx 템플릿](deploy/nginx-classic-bites.conf): 기존 nginx와 연결하는 HTTPS 운영 구성. 현재 서버에는 이 오버레이와 전용 프록시 설정을 적용했습니다.

`AUTH_ENABLED`를 지정하지 않으면 프로세스와 Compose에서 인증을 비활성화합니다. `.env.example`과 `stack.sh install`도 누락된 값을 `false`로 준비하며 기존 `.env`의 값은 보존합니다. HTTPS 또는 보호된 로컬 개발 연결을 준비한 뒤 서버 `.env`에서 `AUTH_ENABLED=true`로 명시적으로 활성화합니다. 설치 스크립트는 누락된 `AUTH_RATE_LIMIT_SALT`를 난수로 추가합니다. Google 설정이 비어 있으면 활성화 후 이메일 인증만 먼저 사용할 수 있고 `/auth/google`은 `503 google_not_configured`를 반환합니다. 실제 OAuth 클라이언트 ID와 HTTPS 주소가 준비되기 전에는 실제 Google·iOS 연동을 완료했다고 판단하지 않습니다.

### 서버 적용 절차

다음은 최초 설치 절차입니다. 기존 정상 서비스에 API만 갱신할 때는 아래 HTTPS 적용 절의 FastAPI 전용 명령을 사용합니다. 기존 `.env`와 데이터 볼륨을 보존하고 정상 백업을 확인합니다. Google 설정은 위 문서를 따라 서버의 비공개 `.env`에 입력하며 보호된 연결 준비 후에만 `AUTH_ENABLED=true`로 활성화합니다. 이번 실제 적용·검증 결과는 이 절의 서버 배포 기록에 있습니다.

```sh
# 소스와 이미지 변경 적용: 누락 환경 설정 추가 → 이미지 빌드 → 기반 서비스
# 준비 → 미디어 권한 → 인증 테이블 준비 → API 시작
./stack.sh install
./stack.sh check
```

인증 테이블 준비만 명시적으로 실행할 때는 빌드된 새 API 이미지와 실행 중인 MySQL이 필요합니다.

```sh
docker compose run --rm --no-deps fastapi python migrate_auth.py
```

이 마이그레이션은 새 `auth_` 테이블 네 개를 만들고 기존 테이블·파일을 삭제하거나 바꾸지 않습니다. MySQL DDL은 전체 트랜잭션 롤백이 되지 않으므로 중간 실패 시 원인을 해결한 뒤 같은 명령을 재실행합니다. 준비 후 `/ready`는 `AUTH_ENABLED=true`일 때 `auth_schema`도 확인합니다. `./stack.sh check`는 기존 미디어 서비스 검사이며 회원 테스트는 아래 별도 명령을 사용합니다.

### 기존 nginx를 통한 HTTPS 적용

다음은 적용·재적용 절차이며 실제 실행 결과는 아래 서버 배포 기록과 구분합니다. 기본 `compose.yaml`의 공개 포트 설정은 유지하고 `compose.https.yaml`을 함께 적용할 때 FastAPI의 호스트 포트를 `127.0.0.1`로 교체합니다. `!override`에는 Docker Compose 2.24.4 이상이 필요합니다. 포트 목록에 loopback 항목만 추가하면 기존 공개 포트도 남으므로 반드시 이 오버레이를 사용합니다. [Compose 병합 규칙](https://docs.docker.com/reference/compose-file/merge/)

오버레이는 FastAPI를 기존 기본 네트워크와 외부 `xixicash-net`에 연결하고 nginx가 `classic-bites-api:8000`으로 접근하게 합니다. MySQL·MinIO는 프록시 네트워크에 추가하지 않습니다. 기존 `xixi-proxy`가 해당 네트워크에 연결되어 있어야 합니다. [외부 네트워크 연결](https://docs.docker.com/compose/how-tos/networking/)

1. 서버 내부의 별도 비공개 폴더에 기존 소스·Compose·`.env`·nginx 설정을 백업하고 정상 DB 백업과 기존 이미지 복구 가능 여부를 확인합니다. `.env`와 인증서 개인 키를 로컬 저장소로 가져오거나 출력하지 않습니다. MySQL·MinIO와 기존 프록시 서비스의 컨테이너 ID·시작 시각도 확인합니다. 기존 앱 DB 계정에 스키마 준비용 `CREATE`·`REFERENCES`와 API용 `SELECT`·`INSERT`·`UPDATE`·`DELETE` 권한이 있는지 확인합니다. 권한 부족을 계정 재생성이나 볼륨 초기화로 해결하지 않습니다.
2. 추적하는 소스만 서버에 전달하고 기존 `.env`와 볼륨을 보존합니다. `python3 scripts/init-env.py`로 누락 설정만 준비하고 `AUTH_ENABLED=false`를 유지합니다. 서버의 비공개 `.env`에 `COMPOSE_FILE=compose.yaml:compose.https.yaml`을 설정합니다. Linux의 Compose 파일 구분자는 `:`입니다. 이후 기존 `stack.sh`와 `docker compose` 명령 모두 동일한 오버레이를 사용합니다. 명시적 `-f compose.yaml` 또는 다른 프로세스의 `COMPOSE_FILE`로 이 설정을 우회하지 않습니다.
3. 기존 `xixi-proxy`의 `xixicash-net` IPv4 주소를 확인해 `AUTH_TRUSTED_PROXY_IP`에 **그 주소 하나만** 설정합니다. Uvicorn은 이 IP에서 들어온 전달 헤더만 신뢰하며 nginx는 `X-Forwarded-For`를 실제 접속 IP로 덮어씁니다. `*`나 프록시 네트워크 전체 CIDR은 허용하지 않습니다. `GOOGLE_CLIENT_IDS`에는 앱이 사용하는 audience를 설정합니다. 서버 ID 없는 native 방식이면 iOS ID, 서버 ID 지정 방식이면 Web application ID를 허용합니다. 실제 값은 비공개 설정에서만 관리합니다.
4. 다음 명령으로 FastAPI만 빌드하고 네 인증 테이블을 준비한 뒤 교체합니다. `stack.sh install`·`restart`는 전체 서비스가 대상이므로 기존 정상 서비스의 최소 갱신에는 아래 명령을 사용합니다.

```sh
docker compose config --quiet
docker compose build fastapi
docker compose run --rm --no-deps fastapi python migrate_auth.py
docker compose up -d --no-deps --wait --wait-timeout 180 fastapi
docker compose port fastapi 8000
```

5. 마지막 명령의 바인딩이 `127.0.0.1`인지, FastAPI가 기본·프록시 네트워크 모두에 연결되는지 확인합니다. 인증 비활성 상태에서는 `/ready`가 인증 테이블을 검사하지 않으므로 마이그레이션의 스키마 검증 결과도 확인합니다. `/health`·`/ready`와 OpenAPI 버전·인증 경로, `/auth/me`의 `503 auth_disabled`를 확인합니다.
6. 기존 nginx의 `/home/xixi/xixi/xixi-proxy/conf.d`에 고전한입 전용 설정을 추가합니다. [템플릿](deploy/nginx-classic-bites.conf)은 nginx 내부의 `/var/www/certbot`·`/etc/letsencrypt`를 사용하므로 기존 호스트 경로 `/home/xixi/certbot/www`·`/home/xixi/certbot/conf`가 각각 연결되어 있는지 확인합니다. 유효한 `vpn.xixiplay.com` 인증서가 있으면 재사용합니다. 없으면 먼저 템플릿의 HTTP 서버 블록만 적용해 ACME webroot를 제공하고 기존 Certbot 실행 방식·ACME 계정·설정 디렉터리로 해당 도메인 인증서를 발급합니다. 인증서 준비 전 HTTPS 블록을 로드하지 않습니다. 기존 서비스의 인증서·계정·설정을 덮어쓰지 않습니다.
7. 인증서 준비 후 전체 템플릿을 적용하고 **기존 nginx 컨테이너 안에서 `nginx -t` 성공 후에만 `nginx -s reload`**합니다. 프록시 전체 재생성은 필요하지 않습니다. 템플릿은 HTTP에서 ACME 파일만 제공하고 나머지 요청은 HTTPS로 이동하며, API는 HTTPS에서만 프록시합니다. Docker DNS를 요청 시 다시 조회해 FastAPI 재생성 후에도 새 주소로 연결합니다. 업로드·다운로드 버퍼링과 프록시 자동 재시도를 끄므로 스트림 처리와 한 번만 쓰는 refresh 요청을 보존합니다.
8. 외부에서 인증서 호스트·유효기간·신뢰와 `https://vpn.xixiplay.com/health`, `/ready`, `/openapi.json`을 확인합니다. 공개 `:8000`으로 직접 접근할 수 없는지와 HTTP에서 `/auth/` 요청을 API로 전달하지 않는지도 확인합니다. 모든 경로가 준비된 후에만 서버 `.env`의 `AUTH_ENABLED=true`를 설정하고 4번의 `up --no-deps` 명령으로 FastAPI만 다시 적용합니다. 활성화 후 `/ready`의 `auth_schema=ok`, 실제 Google 가입·재로그인·세션 복구·로그아웃과 기존 미디어 API를 확인합니다. `./stack.sh check`는 임시 검사 데이터를 만들고 정리하며, DB 검사에는 `DROP` 권한도 필요합니다.

`xixi-proxy`를 재생성하면 IP가 바뀔 수 있습니다. 프록시 IP를 다시 확인하고 서버 `AUTH_TRUSTED_PROXY_IP`를 갱신한 뒤 FastAPI를 재적용합니다. IP가 맞지 않으면 전달 IP를 신뢰하지 않아 회원 요청 제한이 프록시 IP 하나로 합쳐집니다. 프록시 네트워크의 임의 컨테이너를 신뢰 대상으로 확대하지 않습니다.

문제 발생 시 먼저 `AUTH_ENABLED=false`로 인증을 중지하고 FastAPI만 재적용합니다. 필요하면 백업한 소스·Compose·이전 이미지를 복구해 FastAPI만 다시 생성하되 새 인증 테이블과 기존 볼륨은 보존합니다. `.env` 복구 시 현재 자격증명을 유지하고 공개 HTTP 바인딩으로 돌아가기 전에 인증 비활성을 확인합니다. nginx 설정은 고전한입 변경만 복구하고 `nginx -t` 후 reload합니다. 다른 서비스 설정·컨테이너·인증서나 데이터 볼륨을 삭제하지 않습니다.

인증 기록 정리는 한 번에 만료 세션과 요청 제한 기록을 각각 최대 500개 처리합니다. 회원은 삭제하지 않으며 사용한 refresh token 이력은 세션의 절대 만료 전까지 유지합니다. 예약 실행은 아직 설정하지 않았습니다.

```sh
docker compose exec -T fastapi python prune_auth.py
```

### 로컬 검증

Python 3.13과 Docker, 로컬에 준비한 `mysql:8.4` 이미지가 필요합니다(`docker pull mysql:8.4`). 가상환경이 없으면 `python3.13 -m venv .venv`로 먼저 만듭니다. 첫 두 테스트는 외부 서비스·실제 Google 계정 없이 실행합니다. 마지막 테스트는 별도 임시 MySQL 컨테이너만 만들고 종료 후 정리하며 기존 서버·컨테이너·볼륨을 사용하지 않습니다.

```sh
.venv/bin/python -m pip install -r api/requirements-test.txt
.venv/bin/python -m pip check
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/check-auth.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/check-media-limits.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/check-auth-mysql.py
docker compose --env-file .env.example config --quiet
bash -n stack.sh
git diff --check
```

2026-09-29 로컬 검증 환경은 Python `3.13.13`, 임시 MySQL `8.4.10`입니다. 기존 배포 설정의 Python `3.13.15`·MySQL `8.4.11`과 구분합니다.

| 실행한 검사 | 결과 |
| --- | --- |
| `scripts/check-auth.py` | 28개 통과: 인증 API, 오류 정보 보호, Google 실제 RSA 서명 검증, 권한 분리 |
| `scripts/check-auth-mysql.py` | 11개 통과: HTTP→MySQL 흐름, 중복·동시 갱신, 만료·재사용·롤백, 스키마 반복 적용, 정리; 임시 컨테이너 제거 |
| `scripts/check-media-limits.py` | 기존 파일 처리 11개 통과 |
| `pip check`, 전체 Python 구문 검사 | 통과 |
| `docker compose --env-file .env.example config --quiet`, `bash -n stack.sh` | 통과 |
| 환경 초기화·보존 검사 | 새 비밀값 생성·파일 권한, 반복 실행과 기존 자격증명·명시 설정 보존 통과 |
| 로컬 문서 링크, `git diff --check`, `git diff --cached --check` | 통과 |
| `docker build -t classic-bites-auth-check:local api` | 기본 이미지 메타데이터 조회에서 진행되지 않아 중단; 새 이미지 빌드 미검증 |

2026-09-29 로컬 개발 당시 총 50개 자동 테스트가 통과했습니다. 테스트 도구의 `httpx` 사용에 대한 Starlette 변경 예고 경고가 있지만 실패는 아닙니다. 당시에는 운영 서버 배포·실제 Google 계정·iOS 연결을 실행하지 않았습니다. 현재 서버 배포 결과는 다음 기록을 확인합니다.

인증 테스트의 Google 검증은 테스트용 RSA 키로 서명한 ID token과 테스트 인증서를 사용합니다. 실제 검증 라이브러리의 서명·발급자·대상·만료 검사를 확인하지만 실제 Google 로그인창·동의 화면·클라이언트 설정 검증을 대신하지 않습니다.

### 2026-09-30 서버 배포·검증 기록

기존 SSH 접속과 서버의 Docker Compose `v5.1.4`를 사용했습니다. 원본 소스·서버 환경 설정은 서버 내부의 권한 `700`인 별도 배포 백업 폴더에 보관하고 MySQL 논리 백업을 확보했습니다. `.env`·인증서 개인 키·백업을 로컬 저장소로 복사하지 않았으며 기존 자격증명과 데이터 볼륨을 보존했습니다.

| 실행한 검사·적용 | 결과 |
| --- | --- |
| FastAPI 이미지 빌드·`pip check` | `classic-bites-fastapi:1.2.0` 빌드 및 의존성 검사 통과 |
| 인증 마이그레이션·스키마 | 네 `auth_` 테이블 준비·필수 컬럼 검증 통과 |
| FastAPI만 교체·인증 활성화 | `AUTH_ENABLED=true`, native iOS audience 허용 목록 적용; 실제 ID는 비공개 서버 설정에 보관 |
| Compose HTTPS 오버레이 | 호스트 `127.0.0.1:8000`만 바인딩, 기본·프록시 네트워크 연결, 정확한 기존 프록시 IP만 신뢰 |
| 기존 nginx·인증서 | 기존 ACME 계정으로 도메인 인증서 발급, 기존 nginx에서 `nginx -t`·reload 성공 |
| 외부 HTTPS | 기본 CA·호스트 검증을 유지한 연결에서 `/health`·`/ready` HTTP 200, `auth_schema=ok` |
| 외부 OpenAPI | 버전 `1.2.0`과 인증 경로 여섯 개 확인 |
| 인증 실패 처리 | Bearer 없는 `/auth/me`는 `401 invalid_token`, 유효하지 않은 Google ID token은 `401 invalid_google_token` |
| 공개 HTTP 제한 | 외부 `:8000` 접속 시간 초과, HTTP `:80` 인증 경로는 HTTPS로 `308` 이동하며 API에 평문 요청을 전달하지 않음 |
| `./stack.sh check` | DB·미디어 통합 검사와 자원 검사 11개 통과; 임시 객체·검사 상태 정리 완료 |
| 기존 서비스·데이터 | FastAPI 외 기존 12개 컨테이너 ID·시작 시각·실행 상태 동일; MySQL·MinIO 볼륨 유지 |
| 인증서 갱신 설정 | 기존 사용자 crontab의 Certbot 전체 갱신·nginx reload 작업 재사용; 추가 일정 없음 |
| 신규 도메인 인증서 갱신 검사 | `certbot renew --cert-name vpn.xixiplay.com --dry-run --no-random-sleep-on-renew --non-interactive` 종료 코드 0; 기존 인증서·제공 환경 변경 없음 |

사용자가 실제 iPhone에서 Google 로그인과 서재 진입 성공을 확인했습니다. 에이전트가 검증한 서버 상태·잘못된 인증 정보 거부와 사용자가 확인한 실제 계정 로그인을 구분합니다. 첫 가입 여부·재로그인·앱 재실행 후 세션 복구·로그아웃의 추가 확인은 남아 있습니다.

사용자는 이번 Google 로그인 수정의 실기기 테스트 통과를 확인했습니다. 일반/Google 가입 정책, 회원가입·자동 로그인·로그아웃의 추가 확인은 남아 있습니다. 이메일 인증 메일·비밀번호 재설정·계정 연결·탈퇴는 이번 범위에 포함하지 않았습니다. push는 사용자가 직접 수행합니다.


## 12. 서재·콘텐츠 관리 API와 독립 admin

아래 카탈로그 최초 구현·배포의 API·이미지 버전은 `1.3.0`입니다. 공개 서재와 콘텐츠 관리 API의 로컬 검증에 이어 2026-09-30 운영에 배포하고 서재를 활성화했습니다. 이후 대학 콘텐츠 등록 시점의 운영 서버는 `1.4.0`으로 확인했으며, 이번 등록은 기존 서버 버전과 병음 확장 컬럼을 유지한 데이터 추가입니다. 아래 운영 배포 기록과 로컬 검증 기록을 구분하며, 위 11절의 `1.2.0`은 앞서 수행한 인증·HTTPS 배포 기록입니다. 관리자 화면은 별도 [classic-bites-admin](https://github.com/xixirepository/classic-bites-admin) 저장소이며 로컬 경로는 `/Users/sean/xcode/classic-bites-admin`입니다. 해당 프로젝트의 실행 방법은 그 저장소 README를 따릅니다. 이 백엔드는 `/admin/api` 데이터 API를 제공하며, 관리자 화면의 소스·서버·운영 주소는 별도로 관리합니다.

서재에는 사서오경을 한 권으로 표시하고 대학·중용·논어·맹자·시경·서경·역경·예기·춘추는 그 안의 작품으로 둡니다. 다른 책도 관리자에서 추가할 수 있습니다. 책은 작품을 통해 장으로 이동하거나 작품 구분 없이 직접 장을 가질 수 있습니다. 장 안의 한입에는 원문·한국어 번역·해설을 저장합니다. 새로 공개한 책과 내용은 iOS가 다음에 조회할 때 반영되며 제목별 고정 목록을 서버 계약으로 사용하지 않습니다.

- [서재·콘텐츠 API 계약](docs/catalog-api.md): 경로·모델·필드 제한·정렬·공개 정책·오류·표지·마이그레이션.
- `api/catalog.py`, `api/catalog_store.py`: 게스트 조회와 관리자 입력, MySQL 영구 저장, 공개 조상 확인, 안정적인 UUID와 순서.
- `api/catalog_media.py`, `api/catalog_guard.py`: MinIO 표지 업로드·읽기, 요청 크기·시간 제한, 캐시 방지, 별도 관리자 origin 검증.
- `api/migrate_catalog.py`, `api/migrations/002_catalog.sql`: `catalog_books`, `catalog_works`, `catalog_chapters`, `catalog_bites` 네 테이블과 외래키·인덱스, 선택 제목 seed.

### 설정과 권한

| 설정 | 기본값·용도 |
| --- | --- |
| `CATALOG_ENABLED` | `false`. 마이그레이션 후 `true`로 명시적으로 활성화 |
| `CATALOG_ADMIN_USER_IDS` | 빈 목록. 기존 인증 회원 UUID를 쉼표로 구분하여 관리자 지정 |
| `CATALOG_ADMIN_ORIGINS` | 빈 목록. 독립 관리자 화면의 정확한 origin을 쉼표로 구분하여 허용 |

`.env.example`·Compose·환경 초기화 스크립트는 비활성 기본값을 사용하고 기존 `.env`의 명시 설정을 보존합니다. 관리자 지정은 기존 인증으로 생성한 회원의 UUID를 서버의 비공개 설정에 넣는 방식이며, 이메일·이름·앱 플래그로 승격하지 않습니다. 관리자 API에는 `AUTH_ENABLED=true`와 유효한 기존 access token도 필요합니다. 일반 로그인 사용자와 게스트는 관리 API를 사용할 수 없습니다. `MEDIA_API_KEY` 역시 관리자 계정 인증을 대체하지 않습니다.

운영 관리자 origin은 HTTPS를 사용합니다. 로컬 개발에는 `http://localhost:<port>` 또는 `http://127.0.0.1:<port>`를 허용하며 와일드카드·경로 포함 URL·원격 HTTP는 허용하지 않습니다. `CATALOG_ADMIN_ORIGINS`의 CORS 허용 여부와 실제 관리자 권한 검사는 별개입니다. 관리자 브라우저는 API 서버로 요청하며 DB·MinIO 자격증명을 받지 않습니다. 운영 관리자 주소·HTTPS 배포는 미정입니다.

### 공개·준비 상태와 표지

`GET /catalog/books`부터 책·작품·장·한입 상세까지 게스트로 조회할 수 있습니다. 공개 목록과 직접 ID 조회 모두 모든 상위 항목의 공개 상태를 확인합니다. 비공개 책의 하위 본문·표지도 404로 차단합니다. 목록 순서는 `sort_order`, 같은 순서에서는 UUID로 정렬합니다.

초안 저장과 공개·공개 취소를 구분합니다. 준비된 본문이 없는 공개 책·작품·장도 탐색할 수 있으며, `is_ready`는 실제 읽을 텍스트가 있는 공개 한입의 존재로 서버가 계산합니다. 본문이 비어 있는 공개 한입의 상세 조회는 `409 content_preparing`입니다. 공개 취소 후 다음 요청부터 보이지 않도록 콘텐츠·표지·오류 응답에 `no-store`를 적용합니다. 이미 받은 화면 내용을 원격에서 삭제하는 실시간 푸시는 포함하지 않습니다.

책·작품 표지는 관리자 인증 후 PNG·JPEG·WebP 시그니처를 확인하고 5 MiB 이하 파일을 기존 MinIO 비공개 버킷의 `images/catalog/`에 저장합니다. 공개용 표지 API는 다시 공개 상태를 검사하여 바이트를 제공합니다. MinIO 키나 미디어 관리 비밀키를 iOS에 전달하지 않습니다. 기존 표지를 교체해도 이전 객체를 자동 삭제하지 않으며, DB 연결 실패 뒤 남은 객체의 정리는 별도 운영 항목입니다.

### 기존 서버 적용 절차

아래는 적용·재적용 절차이며 실제 실행 결과는 이 절의 운영 배포 기록을 확인합니다. 위 11절의 기존 `.env`·소스·DB·이미지 백업, 정확한 프록시 IP·HTTPS 오버레이·기존 서비스 보존 절차를 함께 따릅니다. 서버의 기존 정상 MySQL·MinIO·nginx를 재생성하지 않고 FastAPI만 갱신합니다. 아래 모든 Compose 명령에도 기존 `COMPOSE_FILE=compose.yaml:compose.https.yaml`이 적용되어야 합니다.

1. 추적하는 새 소스를 서버에 준비하고 `python3 scripts/init-env.py`로 누락 설정만 추가합니다. 기존 자격증명·인증 활성값·프록시 설정을 유지하며 서재는 준비가 끝날 때까지 비활성으로 둡니다.
2. 아래 명령으로 설정을 검사하고 API 이미지를 빌드한 뒤 추가 테이블을 준비합니다. 기존 앱 DB 계정에는 스키마 준비에 필요한 `CREATE`·`REFERENCES`와 콘텐츠 읽기·쓰기 권한이 있어야 합니다. 권한 문제를 계정 재생성이나 볼륨 초기화로 해결하지 않습니다.

```sh
docker compose config --quiet
docker compose build fastapi
docker compose run --rm --no-deps fastapi python migrate_catalog.py

# 선택 실행: 사서오경 한 권과 내부 작품 아홉 개의 제목만 공개합니다.
docker compose run --rm --no-deps fastapi python migrate_catalog.py --seed-classics
```

마이그레이션은 추가 테이블만 만들고 기존 인증 데이터·미디어 파일을 변경하지 않습니다. 반복 실행 시 기존 행을 덮어쓰지 않습니다. 선택 seed에도 본문·임의 번역·검증용 한입을 포함하지 않으며, 재실행해도 관리자가 수정한 제목·순서·공개 상태를 보존합니다. MySQL DDL은 전체 롤백되지 않으므로 실패 시 원인을 고친 뒤 같은 명령을 재실행합니다. `stack.sh install`은 인증·서재 마이그레이션을 실행하지만 seed는 실행하지 않습니다.

3. 서버의 비공개 `.env`에서 `CATALOG_ENABLED=true`로 활성화합니다. 콘텐츠 관리를 사용할 때 실제 관리자 UUID·확정한 관리자 origin을 별도로 지정합니다. 공개 조회만 사용할 때는 두 목록을 비워 둘 수 있습니다. 기존 `AUTH_ENABLED=true`와 인증 설정을 보존합니다.
4. FastAPI만 적용한 뒤 HTTPS 연결과 공개·관리 API를 확인합니다.

```sh
docker compose up -d --no-deps --wait --wait-timeout 180 fastapi
docker compose port fastapi 8000
```

호스트 바인딩이 기존 HTTPS 구성의 `127.0.0.1`인지 확인하고, `/health`·`/ready`에서 `catalog_schema=ok`와 기존 의존 서비스 정상 상태를 확인합니다. OpenAPI `1.3.0`·공개 목록·관리자 로그인·일반 회원의 관리 요청 거부·공개 취소를 검증합니다. `./stack.sh check`는 기존 DB·미디어 검증이며 관리자→iOS 콘텐츠 확인을 대체하지 않습니다. 문제 발생 시 우선 `CATALOG_ENABLED=false`로 신규 API를 중지하고 FastAPI만 다시 적용하며 기존 테이블·볼륨·인증 정보를 보존합니다.

### 2026-09-30 서재 운영 배포·검증 기록

운영 API `1.2.0`에는 카탈로그 경로와 테이블이 없어 `/catalog/books`가 HTTP 404를 반환했습니다. 사용자가 제공한 SSH 키로 서버에 연결해 기존 설정과 데이터를 보존하며 API를 갱신했습니다. 배포 전 소스·Compose·환경 설정, MySQL 논리 백업과 이전 이미지를 서버 내부의 비공개 배포 백업 폴더에 보관했으며, 실제 `.env`·DB 백업·개인 키를 로컬 저장소에 복사하지 않았습니다.

기존 서버 파일이 추적 소스 `b848845`와 일치하는지 확인한 뒤 소스 `3dcb07d`의 `api/`, `compose.yaml`, `scripts/init-env.py`, `.env.example`을 배포했습니다. 적용되는 Compose 설정의 차이는 FastAPI 이미지 버전과 카탈로그 환경 변수 세 개로 제한했고 기존 HTTPS 오버레이·인증 활성값·Google 설정·자격증명을 보존했습니다.

| 실행한 검사·적용 | 결과 |
| --- | --- |
| FastAPI 이미지 빌드·`pip check` | `classic-bites-fastapi:1.3.0` 빌드 및 의존성 검사 통과 |
| 추가 마이그레이션 | `migrate_catalog.py` 성공, 카탈로그 테이블 네 개 준비; 기존 인증 데이터 보존 |
| 초기 콘텐츠 | 책이 0개임을 확인하고 `--seed-classics`를 명시 실행; 공개 사서오경 한 권·작품 아홉 개 생성, 장·한입·본문·표지 추가 없음 |
| 카탈로그 활성화 | `CATALOG_ENABLED=true`; `CATALOG_ADMIN_USER_IDS`·`CATALOG_ADMIN_ORIGINS`는 빈 목록 유지 |
| FastAPI만 교체 | MySQL·MinIO·nginx를 포함한 다른 12개 컨테이너 ID·시작 시각·실행 상태 동일 |
| HTTPS·포트 | 기존 오버레이 유지, API 호스트 바인딩 `127.0.0.1:8000` 확인 |
| 외부 실행·준비 상태 | `/health`·`/ready` HTTP 200; MySQL·MinIO·미디어 버킷·`auth_schema`·`catalog_schema` 모두 `ok` |
| 외부 API 계약 | OpenAPI 버전 `1.3.0`과 카탈로그 경로 확인 |
| 공개 서재 | `/catalog/books` HTTP 200, 사서오경 반환; 본문이 없으므로 `is_ready=false` |
| 인증 없는 요청 | `/auth/me`·`/admin/api/books` 모두 HTTP 401 거부 |
| `./stack.sh check` | 종료 코드 0; 실행·준비 상태·문서·OpenAPI HTTP 200, DB·미디어 임시 검증과 미디어 CRUD·자원 검사 11개 통과; 임시 검사 상태 정리 완료 |

연결된 실제 iPhone 14 Pro / iOS 26.5에서 앱의 운영 HTTPS 조회 검사(Debug·Release 각각 1개)와 UI 탐색 검사 1개가 통과했습니다. 공개 책·작품 아홉 개·빈 목차·준비 상태·목록 재조회를 확인하고 고전 서재 → 사서오경 → 대학 → 준비 안내 → 서재 복귀를 직접 자동 조작했습니다. 검증 후 일반 Debug 앱을 재설치·전면 실행했습니다. 이 에이전트 검증을 사용자 테스트 통과로 기록하지 않습니다. 2026-09-30 사용자가 이 서재 복구 작업의 현재 로컬 `dev` 기준 rebase·squash·`--no-ff` 병합을 명시적으로 요청했습니다. 해당 요청을 별도의 사용자 테스트 통과로 기록하지 않으며 통합 결과·커밋은 Git 이력을 기준으로 확인합니다. push는 사용자가 직접 수행합니다. 운영 관리자 계정·origin 지정, 관리자 화면의 HTTPS 배포, 실제 관리자 등록·표지·본문·공개 취소 동선 검증은 남아 있습니다. 이번 배포에 맞춰 기존 로컬 자동 검사 74개 전체를 다시 실행한 것은 아니며, 아래 기록은 앞선 소스 구현 당시의 결과입니다.

### 로컬 자동 검증과 연동 확인

기존 Python 3.13 가상환경에 `api/requirements-test.txt`를 설치합니다. MySQL 검사는 로컬에 준비한 `mysql:8.4` 이미지와 Docker가 필요하며, 기존 서버·환경 파일·볼륨 대신 독립된 임시 컨테이너를 사용하고 종료 후 제거합니다.

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/check-catalog.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/check-catalog-media.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/check-catalog-mysql.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/check-auth.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/check-auth-mysql.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/check-media-limits.py
docker compose --env-file .env.example config --quiet
bash -n stack.sh
git diff --check
```

2026-09-30 이번 소스에서 실행한 결과입니다. 독립 MySQL 검사 버전 `8.4.10`은 배포 구성의 `8.4.11`과 구분합니다.

| 실행한 검사 | 결과 |
| --- | --- |
| `scripts/check-catalog.py` | 8개 통과: 공개 계약, 관리자 권한, 초안·입력·준비중·실패 처리 |
| `scripts/check-catalog-media.py` | 7개 통과: 업로드 파싱 이전 권한 검사, 크기·형식 제한, 공개 취소·누락 표지, 스트림 정리, 오류 캐시 방지·CORS; 가짜 저장소 사용 |
| `scripts/check-catalog-mysql.py` | 9개 통과: 실제 MySQL 저장·재연결, 계층·외래키·정렬·변경 실패 보존, 모든 공개 조상 검사, 실제 인증 HTTP 흐름, 반복 마이그레이션·seed 보존; 임시 컨테이너 제거 |
| `scripts/check-auth.py` | 기존 인증 회귀 28개 통과 |
| `scripts/check-auth-mysql.py` | 기존 실제 MySQL 인증 회귀 11개 통과; 임시 컨테이너 제거 |
| `scripts/check-media-limits.py` | 기존 미디어 자원 처리 11개 통과 |
| Compose 설정·셸 구문 | `docker compose --env-file .env.example config --quiet`, `bash -n stack.sh` 통과 |
| 별도 로컬 MySQL·MinIO·API 기동 | `/ready` 정상 상태 확인 |
| 독립 admin 브라우저→실제 로컬 API·MySQL | 로그인 후 사서오경→대학 아래 검증용 장·한입 생성과 원문·번역·해설 저장 확인 |
| FastAPI 이미지 | `docker build -t classic-bites-fastapi:1.3.0 api` 성공 |
| 관리자→iOS 시뮬레이터 | 실제 관리자 입력 원문·번역·해설 표시, 새 책 공개 후 앱 재빌드 없이 서재 재진입으로 반영 |
| 실제 MinIO 표지·비공개 | 관리자 화면에서 표지 업로드·교체, 실제 바이트 일치·iOS 재진입 갱신, 상위 책 비공개 후 장·한입·표지 직접 조회 404 및 앱 접근 차단·목록 제거 확인 |

신규 24개와 기존 50개로 총 74개 자동 검사를 통과했습니다. API·표지의 모의 저장소 검사, 독립 실제 MySQL 검사, 별도 로컬 MySQL·MinIO와 관리자→iOS 시뮬레이터 연결 확인은 서로 다른 검증입니다. 로컬 연결은 임시 전용 계정·데이터를 사용했으며 운영 데이터에 접속하지 않았습니다. 비공개 전환은 실제 관리 API로 실행하고 iOS에서 직접 진입 차단·서재 재조회 결과를 확인했습니다. Chrome에서는 관리자 공개 전환 확인창의 Escape 취소·키보드 승인과 실제 저장을 확인했습니다. iOS에서는 화면 재진입 갱신을 직접 확인했고, 당겨서 새로고침 제스처의 직접 조작은 남아 있습니다. 해당 로컬 검증 시점에는 운영 배포·관리자 설정·실기기 테스트를 하지 않았으며, 이후 운영 적용 결과는 위 별도 배포 기록을 따릅니다. 학습 기록 저장·결제·본문 저작·Android·사용자용 웹은 이번 범위에 포함하지 않습니다.

사용자 확인은 관리자에서 책·내부 작품·장·한입을 등록하고 공개한 뒤 iOS 새로고침으로 목록과 본문을 확인하는 순서입니다. 제목·정렬·본문·표지 수정 반영, 비공개 시 목록과 직접 조회 차단, 빈 서재·통신 실패·누락 표지 안내, 기존 Google 로그인·게스트 둘러보기도 확인합니다. 자동 검증 성공은 사용자 테스트 승인과 구분하며, 승인 전 통합용 rebase·squash·dev 병합을 하지 않습니다. push는 사용자가 직접 수행합니다.


### 2026-09-30 대학 목차·본문 운영 등록

사용자의 요청으로 사서오경 안의 대학에 **경 1장·전 10장, 원문·한국어 번역·해설 34개 한입**을 등록했다. 판본·한입별 구성·이용 출처·등록 절차는 [대학 콘텐츠 안내](docs/daehak-content.md)에 기록했다. 원문은 주희 『대학장구』를 기준으로 하며 전 5장의 주희 보전을 원래 경전 본문과 구분한다. 번역·해설은 이번에 작성한 학습용 초안이며 전문 감수 전이다.

등록 전 운영 DB의 장·한입이 각각 0개임을 확인하고 카탈로그 논리 백업을 서버 내부 비공개 경로에 저장했다. API `1.4.0`과 기존 11개 컨테이너를 유지한 채 새 등록 도구와 데이터 파일만 서버의 별도 콘텐츠 폴더에 전달했다. `.env`·인증 데이터·표지·책·작품 메타데이터를 변경하지 않았고, 이미지 교체·서비스 재시작은 수행하지 않았다. 최초 등록 당시 병음은 빈 값이었으며 후속 병음 보완 결과는 아래에 구분한다.

| 검증 | 결과 |
| --- | --- |
| `scripts/check-daehak-import.py` | 독립 MySQL 8.4.10에서 11개 통과; 완본·공개 조회·순서·재실행·충돌 보호·실제 실패 롤백·병음 보존 |
| `scripts/check-catalog.py` | 기존 공개·관리 API 검사 8개 통과 |
| `scripts/check-catalog-mysql.py` | 기존 저장·계층·권한·마이그레이션 검사 9개 통과; 임시 컨테이너 제거 |
| 운영 계획 확인·등록·재실행 | `would_create` → `created` → `unchanged`, 11장·34한입; 중복 추가 없음 |
| 외부 HTTPS 조회 48회 | 책·작품·11장·34한입·`/ready` 모두 성공; 원문·번역·해설·순서가 등록 파일과 일치 |
| 공개 준비 상태 | 사서오경·대학·11장·34한입 모두 읽기 가능, 다른 여덟 작품의 준비 상태 유지 |
| 기존 서비스 | 실행 중인 11개 컨테이너 ID 동일, `/ready`의 MySQL·MinIO·미디어·인증·카탈로그 검사 정상 |

앱 소스는 변경하지 않아 Xcode 빌드·실기기 조작은 이번 데이터 등록에서 실행하지 않았다. 사용자 확인 항목은 앱에서 대학 화면을 재조회한 뒤 11개 목차와 첫·중간·마지막 한입의 원문·번역·해설을 읽는 것이다. 최초 등록 검증 시점에는 사용자 확인 전 작업 브랜치 단계였으며 rebase·dev 병합·push를 하지 않았다.

같은 날 사용자가 대학 콘텐츠 작업의 현재 로컬 `dev` 기준 rebase·병합을 명시적으로 요청했다. `dev`(`908be94`) 위에서 충돌이나 코드 변경 없이 rebase를 확인하고, 대학 등록 11개·카탈로그 API 8개·실제 MySQL 카탈로그 9개를 다시 통과했다. 문서 링크·Git 공백 검사도 통과했다. 작업 커밋 하나와 `--no-ff` 병합을 기준으로 하며 최종 통합 이력은 Git에서 확인한다. 이 통합 요청을 별도의 실기기 사용자 테스트 통과로 기록하지 않는다. 운영 콘텐츠를 다시 등록하거나 서버를 재시작하지 않았고 push는 사용자가 직접 수행한다.

### 2026-09-30 대학 한자·병음 보완

후속 요청으로 운영 대학의 작품·11개 장에 한자·병음 제목을 채우고 **34한입, 본문 한자 1,887자에 대응하는 성조 병음**을 등록했다. 기존 병음 표시 앱은 다음 조회부터 이 서버 값을 표시한다. [콘텐츠 안내](docs/daehak-content.md#한자-제목과-병음)에 작성 출처·독음 선택·한자별 음절 표기 원칙과 도구 사용법을 기록했다. 번역·해설과 마찬가지로 병음도 전문 감수 전 학습용 초안이며 `review_status=draft`를 유지했다.

`api/content/daehak.json`에 병음·한자 제목을 추가하고 `api/fill_daehak_pinyin.py`로 빈 읽기 필드만 보완한다. 기존 부모·전체 계층·원문을 잠금 조회하고 다른 병음과 충돌하면 쓰기를 모두 거부한다. 기존 출처·검수 상태·한국어 제목·번역·해설·정렬·공개 상태를 보존한다. 이 도구는 운영에 별도로 배포된 한자·병음 확장 컬럼을 전제로 한다. 현재 브랜치의 API `1.3.0`이나 기본 마이그레이션에 병음 API 구현을 추가한 것은 아니며, 스키마가 없으면 도구가 중단한다.

카탈로그 백업은 서버 내부 비공개 경로에 보관하고 로컬로 복사하지 않았다. 계획 확인에서 작품 필드 5개·장 제목 필드 22개·본문 병음 34개를 확인한 뒤 한 트랜잭션으로 등록했으며 재실행은 `unchanged`였다. API `1.4.0`과 11개 컨테이너의 ID를 유지했다. 앱·서버 코드 배포, 서비스 재시작, 환경 파일 변경은 없었다.

| 검증 | 결과 |
| --- | --- |
| `scripts/check-daehak-pinyin.py` | 독립 MySQL 8.4.10에서 11개 통과; 실제 원고·빈 값 보완·충돌·동시 편집·직렬화·롤백·검수 상태 보존 |
| 기존 등록·카탈로그 회귀 검사 | `check-daehak-import.py` 11개, `check-catalog.py` 8개, `check-catalog-mysql.py` 9개 통과 |
| 콘텐츠 대조 | 기존 원문·번역·해설 등 모든 이전 필드 동일, 본문 한자 수와 병음 음절 수·구두점·문단 일치 |
| 운영 HTTPS | 전체 reader와 34개 개별 한입의 병음이 원고와 일치; 읽기 필드 외 공개 응답은 등록 전과 동일; `/ready` 전체 정상 |
| 운영 DB | 작성 출처 기록, `review_status=draft` 유지, 재실행 변경 0건 |
| iOS 시뮬레이터 UI | 기존 병음 표시 앱 + 운영 서버로 iPhone 17 / iOS 26.3.1 임시 UI 검사 1개 통과. 작품·장 제목, 첫 한입 한자 아래 병음·준비 안내 해소·스크롤·뒤로 가기 확인 및 화면 캡처 검토 |

앱 소스는 변경하지 않았고 기존 병음 앱의 일반 Debug 빌드를 시뮬레이터에 설치해 확인했다. 실제 iPhone 조작이나 사용자 테스트 통과, 전문 감수 완료를 뜻하지 않는다. 사용자는 대학 화면을 나갔다가 다시 열어 첫·중간·마지막 한입의 병음과 성조를 확인한다. 이번 병음 보완은 별도 `feature/daehak-pinyin` 작업 브랜치에서 진행했으며 최초 검증 시점에는 `dev` 통합·push를 실행하지 않았다.

같은 날 사용자가 병음 작업의 현재 로컬 `dev` 기준 rebase·squash·병합을 명시적으로 요청했다. `dev`(`dc8d156`) 위에서 충돌이나 코드 변경 없이 rebase를 확인했고, 이미 기능 커밋 하나였으므로 그 형태를 유지했다. 병음 보완 11개·기존 대학 등록 11개·카탈로그 API 8개·카탈로그 MySQL 9개, 총 39개 검사를 다시 통과했다. 문서 링크·Git 공백 검사도 통과했다. 기능 커밋 하나와 `--no-ff` 병합을 기준으로 하며 최종 통합 이력은 Git에서 확인한다. 이 통합 요청을 별도의 실기기 사용자 테스트 통과로 기록하지 않는다. 코드·콘텐츠에 변경이 없어 운영 등록·서버 재시작·앱 UI 검사를 반복하지 않았으며 push는 사용자가 직접 수행한다.
