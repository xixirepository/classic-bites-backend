# 고전한입 백엔드 · Classic Bites Backend

고전한입의 Python FastAPI API, MySQL, MinIO를 관리하는 독립 저장소입니다. iOS 앱은 별도 `classic-bites-ios` 저장소에서 관리합니다.

기존 서버 배포 위치는 `/home/xixi/classic-bites-stack`이며, 서버 비밀번호와 실제 환경 설정은 서버에서 관리합니다. 이 저장소에는 소스 코드, Docker Compose 구성, 비밀정보를 제거한 설정 예시와 운영 설명서를 관리합니다.

작업은 로컬 `dev`에서 기능 브랜치를 만들어 진행합니다. 사용자가 통합을 승인하면 현재 `dev` 기준으로 rebase하고 작업 커밋을 하나로 정리한 뒤 `--no-ff`로 병합합니다. 원격 push는 사용자가 직접 수행합니다. 상세 작업 규칙은 [AGENTS.md](AGENTS.md)를 따릅니다.
