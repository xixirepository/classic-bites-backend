# 고전한입 백엔드 · Classic Bites Backend
이 저장소는 고전한입 백엔드의 소스와 Docker 운영 구성을 관리합니다. iOS 앱은 별도 `classic-bites-ios` 저장소에서 관리합니다. 작업 규칙은 [AGENTS.md](AGENTS.md)를 따릅니다.

세 서비스를 Docker Compose 프로젝트 하나로 설치하고 함께 시작·중지하는 구성입니다. 서버의 기존 서비스와 구분되는 `classic-bites-stack` 프로젝트를 사용합니다. FastAPI, MySQL, MinIO는 서버의 외부 인터페이스(`0.0.0.0`)에서 각각 아래 포트를 수신하도록 구성합니다. 공유기의 해당 포트 포워딩이 연결되어 있으면 SSH 터널 없이 직접 접속할 수 있습니다.

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
| `stack.sh` | 설치·시작·중지·상태 확인 명령 |
| `.env` | 서버에서 생성하는 실제 비밀번호와 환경 설정 |
| `.env.example` | 비밀번호를 포함하지 않는 설정 예시 |
| `api/` | Python FastAPI 시작 앱과 Docker 빌드 파일 |
| `minio/` | 고정된 MinIO 소스를 빌드하는 Docker 파일 |
| `scripts/` | 설치 보조 및 연결 점검 도구 |
| `README.md` | 이 운영 설명서 |

FastAPI는 `/health`, `/ready`, `/docs`와 미디어 파일 CRUD API를 제공합니다. 회원·학습 API와 MySQL의 파일 정보 테이블은 구현하지 않았습니다. 파일 API는 아래 버킷의 다섯 경로만 사용하며 `X-API-Key` 인증이 필요합니다. FastAPI는 전용 MinIO 계정으로 이 버킷의 파일을 관리합니다. MinIO 관리자 비밀번호는 초기 권한 설정과 설치 점검 프로세스에 표준입력으로 일시 전달하며, FastAPI의 상시 환경 변수에는 넣지 않습니다.

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

`install`은 `.env` 초기 준비, 이미지 빌드, MySQL·MinIO 시작, 미디어 버킷·전용 계정·권한 설정, FastAPI 시작을 순서대로 처리합니다. 기존 `.env`의 비밀번호는 유지하고 새 설정만 추가합니다. 각 서비스의 정상 상태를 기다립니다. 처음 MinIO를 소스에서 빌드할 때는 시간이 걸립니다. `--wait-timeout 180`은 서비스 시작 후 대기 제한이며 전체 이미지 다운로드·빌드 시간 제한은 아닙니다.

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

2026-09-29 사용자 요청에 따라 FastAPI뿐 아니라 MySQL과 MinIO도 외부 인터페이스(`0.0.0.0`)에서 수신하도록 구성합니다. 직접 접속에는 SSH 터널이 필요하지 않습니다.

| 서비스 | 직접 접속 주소 | 서버 호스트 포트 → 컨테이너 포트 |
| --- | --- | --- |
| FastAPI API 문서 | <http://vpn.xixiplay.com:8000/docs> | `0.0.0.0:8000` → `8000` |
| FastAPI 실행 상태 | <http://vpn.xixiplay.com:8000/health> | 동일 |
| FastAPI 의존 서비스 상태 | <http://vpn.xixiplay.com:8000/ready> | 동일 |
| MinIO 웹 콘솔 | <http://vpn.xixiplay.com:9001> | `0.0.0.0:9001` → `9001` |
| MinIO S3 API | `http://vpn.xixiplay.com:9000` | `0.0.0.0:9000` → `9000` |
| MySQL | 호스트 `vpn.xixiplay.com`, 포트 `3307` | `0.0.0.0:3307` → `3306` |

MySQL 접속 프로그램에서는 데이터베이스 `classic_bites`, 사용자 `classic_bites`, `.env`의 `MYSQL_PASSWORD`를 사용합니다. MySQL은 브라우저 주소가 아니라 DB 접속 프로그램에서 연결합니다. MinIO 콘솔에서는 사용자 `classicbitesadmin`, `.env`의 `MINIO_ROOT_PASSWORD`를 사용합니다.

공유기에서 다음 TCP 포트 포워딩이 필요합니다. 서버 내부 주소 `192.168.0.100`은 이번 서버 확인 시점 기준입니다. 공유기 설정 자체는 이번 작업에서 변경하지 않았습니다.

| 외부 TCP 포트 | 대상 서버 | 내부 TCP 포트 |
| --- | --- | --- |
| `8000` | `192.168.0.100` | `8000` |
| `3307` | `192.168.0.100` | `3307` |
| `9000` | `192.168.0.100` | `9000` |
| `9001` | `192.168.0.100` | `9001` |

MinIO의 `MINIO_BROWSER_REDIRECT_URL`은 Compose에서 `http://vpn.xixiplay.com:${MINIO_CONSOLE_PORT:-9001}`로 설정합니다. 기본 설정에서는 S3 API 주소를 브라우저에서 열 때 외부 콘솔 주소 `http://vpn.xixiplay.com:9001`로 이동합니다. 웹 접속 주소는 HTTP이며 HTTPS와 도메인 인증서는 구성하지 않았습니다.

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

Swagger 문서: <http://vpn.xixiplay.com:8000/docs>

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
  http://vpn.xixiplay.com:8000/files

# 이미지 목록
curl --fail-with-body \
  -H "X-API-Key: $MEDIA_API_KEY" \
  'http://vpn.xixiplay.com:8000/files?category=images&limit=20'

# 업로드 응답의 key 값을 입력
read -r -p '파일 key: ' object_key

# 파일 정보
curl --fail-with-body -G \
  -H "X-API-Key: $MEDIA_API_KEY" \
  --data-urlencode "key=$object_key" \
  http://vpn.xixiplay.com:8000/files/info

# 파일 다운로드
curl --fail-with-body -G \
  -H "X-API-Key: $MEDIA_API_KEY" \
  --data-urlencode "key=$object_key" \
  -o downloaded-file \
  http://vpn.xixiplay.com:8000/files/download

# 파일 키를 URL에 안전하게 넣기
export object_key
encoded_key="$(python3 -c 'import os,urllib.parse; print(urllib.parse.quote(os.environ["object_key"], safe=""))')"

# 기존 파일 교체: replacement.jpg를 실제 파일 경로로 변경
curl --fail-with-body -X PUT \
  -H "X-API-Key: $MEDIA_API_KEY" \
  -F file=@replacement.jpg \
  "http://vpn.xixiplay.com:8000/files?key=$encoded_key"

# 파일 삭제
curl --fail-with-body -X DELETE \
  -H "X-API-Key: $MEDIA_API_KEY" \
  "http://vpn.xixiplay.com:8000/files?key=$encoded_key"
unset MEDIA_API_KEY object_key encoded_key
```

파일 API 키는 관리자·서버 간 작업용 공유 키입니다. 이 키를 앱 번들이나 웹 프런트엔드에 넣으면 안 됩니다. 사용자별 로그인·파일 소유권 검사는 별도 구현 대상입니다. 현재 외부 주소는 HTTP이므로 실제 서비스에서는 HTTPS를 구성하고, 그 전에는 SSH 터널을 이용해 인증 요청을 보호할 수 있습니다.

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
    compose.yaml stack.sh .env.example README.md api minio scripts
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
- `/ready`는 MySQL의 `SELECT 1`, MinIO의 `/minio/health/cluster`, 전용 S3 계정으로 미디어 버킷 접근을 확인합니다. `checks`에는 `mysql`, `minio`, `media_bucket` 결과가 있습니다.
- `./stack.sh check`는 기반 서비스의 임시 DB·S3 점검, 파일 CRUD HTTP 통합 검사, 업로드·연결 종료 처리 테스트 11개를 실행합니다. 검사는 직접 만든 파일만 정리합니다. 기반 점검 실패로 `.smoke-state.json`이 남으면 원인을 해결한 뒤 `python3 scripts/smoke.py cleanup`으로 해당 점검 데이터만 정리하고 다시 실행합니다. 파일 CRUD 검사에서 정리에 실패하면 남은 검사 파일 키를 출력합니다.
- `unhealthy`나 시작 대기 시간 초과가 나오면 해당 서비스 로그를 확인합니다. 첫 MySQL 초기화나 MinIO 빌드가 진행 중인지도 확인하세요.
- `Address already in use`가 SSH 터널에서 나오면 내 컴퓨터의 해당 포트를 확인하고 터널의 왼쪽 포트를 바꿉니다. 서버에서 나오면 다른 서비스의 포트를 바꾸지 말고 이 Compose의 충돌을 확인합니다.
- 직접 접속이 거부되거나 시간 초과가 나면 서버의 컨테이너와 포트 수신 상태, 공유기의 해당 TCP 포트 포워딩을 확인합니다. SSH 터널을 사용할 때는 터널도 유지되는지 확인합니다.
- MySQL 인증 오류가 나면 `.env`와 실제 DB 계정 비밀번호가 일치하는지 확인합니다. 데이터 볼륨을 삭제하여 해결하지 마세요.
- `docker compose config`의 일반 출력에는 환경 변수의 비밀번호가 포함될 수 있습니다. 공유용 점검에는 `docker compose config --quiet`를 사용하고, 로그에도 비밀번호가 포함되지 않았는지 확인한 뒤 전달하세요.

## 9. 버전 및 설치 검증 기록

설치·검증일: 2026-09-29. 세 서비스 설치를 완료했으며 최종 상태는 모두 `healthy`입니다. 아래 결과는 실제 서버와 SSH 터널을 통해 확인했습니다.

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

운영 요구가 커질 때에는 유지보수 가능한 저장소 대안, 외부 HTTPS 공개 여부, 사용자별 인증·파일 소유권, 백업 주기·보존 기간·복구 시험, 서버 자원과 모니터링을 별도 결정해야 합니다. 이 구성은 iOS 저장소와 분리된 백엔드 전용 저장소에서 관리합니다. 위 표는 2026-09-29 서버 설치·CRUD 적용 시점의 검증 기록이며, 로컬 Git 이력 정리를 위해 서버를 재배포하거나 재시작하지 않습니다.

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
