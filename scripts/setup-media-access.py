#!/usr/bin/env python3
"""Provision the media bucket and dedicated account without retaining admin secrets in the API container."""

import inspect
import json
import os
from pathlib import Path
import re
import subprocess
import sys


def bootstrap():
    # This function runs inside a one-off FastAPI image, not in the host Python.
    import io
    import json
    import re
    import sys

    from minio import Minio
    from minio.credentials import StaticProvider
    from minio.error import MinioAdminException, S3Error
    from minio.minioadmin import MinioAdmin
    import urllib3

    def admin_code(error):
        try:
            return json.loads(error._body).get("Code", "")
        except (ValueError, AttributeError):
            return ""

    def normalized(value):
        if isinstance(value, dict):
            return {key: normalized(item) for key, item in value.items() if not (key in {"Sid", "Id", "Condition"} and not item)}
        if isinstance(value, list):
            return sorted((normalized(item) for item in value), key=lambda item: json.dumps(item, sort_keys=True))
        return value

    stage = "read bootstrap configuration"
    try:
        payload = json.load(sys.stdin)
        env = payload["env"]
        bucket = env["MINIO_BUCKET"]
        access_key = env["MINIO_ACCESS_KEY"]
        policy_name = bucket + "-crud"
        pool = urllib3.PoolManager(timeout=urllib3.Timeout(connect=5, read=30), retries=urllib3.Retry(total=2))
        admin = MinioAdmin(
            endpoint="minio:9000", secure=False, http_client=pool,
            credentials=StaticProvider(env["MINIO_ROOT_USER"], env["MINIO_ROOT_PASSWORD"]),
        )
        owner = Minio(
            "minio:9000", secure=False, http_client=pool,
            access_key=env["MINIO_ROOT_USER"], secret_key=env["MINIO_ROOT_PASSWORD"],
        )
        app = Minio(
            "minio:9000", secure=False, http_client=pool,
            access_key=access_key, secret_key=env["MINIO_SECRET_KEY"],
        )

        stage = "inspect existing application policy"
        try:
            stored = json.loads(admin.policy_info(policy_name))
        except MinioAdminException as error:
            if admin_code(error) not in {"XMinioAdminNoSuchPolicy", "NoSuchPolicy"}:
                raise
            stored = None
        if stored is not None and normalized(stored) != normalized(payload["policy"]):
            raise RuntimeError("An existing policy with this name differs; it was preserved. Choose a dedicated policy/account before retrying.")

        stage = "inspect existing application user"
        try:
            user_info = json.loads(admin.user_info(access_key))
        except MinioAdminException as error:
            if admin_code(error) not in {"XMinioAdminNoSuchUser", "NoSuchUser"}:
                raise
            user_info = None
        if user_info is not None and user_info.get("status") != "enabled":
            raise RuntimeError("Existing application user is disabled; it was preserved. Resolve its status before retrying.")

        stage = "create or verify private media bucket"
        if not owner.bucket_exists(bucket):
            owner.make_bucket(bucket)
        try:
            bucket_policy = json.loads(owner.get_bucket_policy(bucket))
        except S3Error as error:
            if error.code != "NoSuchBucketPolicy":
                raise
        else:
            if bucket_policy.get("Statement"):
                raise RuntimeError("The media bucket already has a bucket policy; it was preserved. Review its privacy before retrying.")

        stage = "create media prefix markers"
        for prefix in ("images/", "videos/", "audio/", "documents/", "subtitles/"):
            try:
                owner.stat_object(bucket, prefix)
            except S3Error as error:
                if error.code not in {"NoSuchKey", "NoSuchObject", "NotFound"}:
                    raise
                owner.put_object(bucket, prefix, io.BytesIO(b""), 0)

        stage = "create scoped application policy"
        if stored is None:
            admin.policy_add(policy_name, policy=payload["policy"])
        stage = "create application user"
        if user_info is None:
            admin.user_add(access_key, env["MINIO_SECRET_KEY"])
            user_info = {"policyName": ""}
        # Attaching a policy preserves every other existing association.
        policies = {item.strip() for item in user_info.get("policyName", "").split(",") if item.strip()}
        stage = "attach scoped application policy"
        if policy_name not in policies:
            admin.attach_policy([policy_name], user=access_key)

        stage = "verify application credentials and bucket access"
        # list_objects is lazy; advancing the iterator makes a signed S3 call.
        next(app.list_objects(bucket, prefix="images/", recursive=True), None)
        print("Media access ready: private bucket, five prefixes, and dedicated application credentials verified.")
        if policies - {policy_name}:
            print("Existing application user's additional policy associations were preserved; review them separately if narrower permissions are required.")
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
    except Exception as error:
        # Never print SDK exception bodies: they may contain request details.
        code = admin_code(error) if isinstance(error, MinioAdminException) else getattr(error, "code", "")
        safe_code = code if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_]{1,80}", code) else type(error).__name__
        print(f"Media bootstrap failed during {stage} ({safe_code}). Existing credentials were not overwritten.", file=sys.stderr)
        raise SystemExit(1) from None


def compose_credentials(root, child_env):
    path = root / ".env"
    if path.is_symlink() or not path.is_file():
        raise SystemExit("Run ./stack.sh install to create a regular private .env first.")
    path.chmod(0o600)
    # Compose owns .env interpolation/quoting rules and shell-variable precedence.
    # Capture the resolved configuration only in memory; never log its contents.
    result = subprocess.run(
        ["docker", "compose", "config", "--format", "json"],
        cwd=root, env=child_env, capture_output=True, text=True, check=False,
    )
    if result.returncode:
        raise SystemExit("Cannot resolve Docker Compose configuration; verify required .env settings.")
    try:
        services = json.loads(result.stdout)["services"]
        owner = services["minio"]["environment"]
        app = services["fastapi"]["environment"]
        return {
            "MINIO_ROOT_USER": owner.get("MINIO_ROOT_USER"),
            "MINIO_ROOT_PASSWORD": owner.get("MINIO_ROOT_PASSWORD"),
            "MINIO_ACCESS_KEY": app.get("MINIO_ACCESS_KEY"),
            "MINIO_SECRET_KEY": app.get("MINIO_SECRET_KEY"),
            "MINIO_BUCKET": app.get("MINIO_BUCKET"),
        }
    except (ValueError, KeyError, TypeError):
        raise SystemExit("Docker Compose configuration does not contain the required MinIO and FastAPI settings.") from None


def main():
    root = Path(__file__).resolve().parent.parent
    child_env = os.environ.copy()
    env = compose_credentials(root, child_env)
    needed = ("MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "MINIO_BUCKET")
    for key in needed:
        if not env.get(key) or env[key] == "GENERATE_ON_INSTALL":
            raise SystemExit(f"Missing private setting {key}; run ./stack.sh install first.")
    bucket = env["MINIO_BUCKET"]
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", bucket):
        raise SystemExit("MINIO_BUCKET must contain 3-63 lowercase letters, digits or hyphens.")
    if env["MINIO_ACCESS_KEY"] == env["MINIO_ROOT_USER"]:
        raise SystemExit("MINIO_ACCESS_KEY must identify a dedicated user, not the MinIO root user.")
    policy = json.loads((root / "minio/media-policy.json").read_text().replace("__BUCKET_NAME__", bucket))
    payload = json.dumps({"env": {key: env[key] for key in needed}, "policy": policy})
    code = inspect.getsource(bootstrap) + "\nbootstrap()\n"
    command = ["docker", "compose", "run", "--rm", "-T", "--no-deps", "--entrypoint", "python", "fastapi", "-c", code]
    result = subprocess.run(command, cwd=root, env=child_env, input=payload, text=True, check=False)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
