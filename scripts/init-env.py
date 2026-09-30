#!/usr/bin/env python3
import os
from pathlib import Path
import re
import secrets


SECRET_KEYS = (
    "MYSQL_PASSWORD", "MYSQL_ROOT_PASSWORD", "MINIO_ROOT_PASSWORD",
    "MINIO_SECRET_KEY", "MEDIA_API_KEY", "AUTH_RATE_LIMIT_SALT",
)
API_DEFAULTS = {
    "MINIO_ACCESS_KEY": "classicbitesapi",
    "MINIO_BUCKET": "classic-bites-media",
    "MAX_UPLOAD_BYTES": "104857600",
    "AUTH_ENABLED": "false",
    "AUTH_ACCESS_TTL_SECONDS": "900",
    "AUTH_REFRESH_TTL_SECONDS": "2592000",
    "GOOGLE_CLIENT_IDS": "",
    "CATALOG_ENABLED": "false",
    "CATALOG_ADMIN_USER_IDS": "",
    "CATALOG_ADMIN_ORIGINS": "",
}


def entries(content):
    result = {}
    for line in content.splitlines():
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$", line)
        if match:
            key, value = match.groups()
            if key in result:
                raise SystemExit(f"Duplicate .env key: {key}; resolve it before installing.")
            result[key] = value.strip().strip("\"'")
    return result


def main():
    root = Path(__file__).resolve().parent.parent
    path = root / ".env"
    if path.is_symlink():
        raise SystemExit("Refusing non-regular .env")
    if path.exists():
        if not path.is_file():
            raise SystemExit("Refusing non-regular .env")
        path.chmod(0o600)
        content = path.read_text()
        current = entries(content)
        for key in SECRET_KEYS:
            if key in current and current[key] in ("", "GENERATE_ON_INSTALL"):
                raise SystemExit(f"Existing {key} is empty or a placeholder; set it explicitly. Existing credentials were not overwritten.")
        missing_base = [key for key in SECRET_KEYS[:3] if key not in current]
        if missing_base:
            raise SystemExit("Existing .env is missing database/root credentials; restore it before installing.")
        additions = {key: value for key, value in API_DEFAULTS.items() if key not in current}
        for key in ("MINIO_SECRET_KEY", "MEDIA_API_KEY", "AUTH_RATE_LIMIT_SALT"):
            if key not in current:
                additions[key] = secrets.token_hex(32)
        if additions:
            with path.open("a") as stream:
                if content and not content.endswith("\n"):
                    stream.write("\n")
                stream.write("\n# API credentials and settings; keep this file private.\n")
                stream.writelines(f"{key}={value}\n" for key, value in additions.items())
            print("Added missing API settings (mode 600); existing credentials unchanged.")
        else:
            print("Existing .env retained (mode 600); credentials unchanged.")
        return

    content = (root / ".env.example").read_text()
    content = content.replace(
        "# Non-secret defaults. stack.sh install generates new independent passwords.",
        "# Private credentials. Do not commit, share, or paste into logs.",
    )
    current = entries(content)
    for key in SECRET_KEYS:
        if current.get(key) == "GENERATE_ON_INSTALL":
            content = re.sub(rf"(?m)^{key}=GENERATE_ON_INSTALL$", key + "=" + secrets.token_hex(32), content)
        elif key not in current:
            content += f"\n{key}={secrets.token_hex(32)}\n"
    for key, value in API_DEFAULTS.items():
        if key not in current:
            content += f"{key}={value}\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(content)
    print("Created private .env with independent random credentials (mode 600).")


if __name__ == "__main__":
    main()
