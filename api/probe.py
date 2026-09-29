"""Private deployment probe, invoked via docker compose exec; no HTTP route."""
import io
import json
import re
import sys

import pymysql
from minio import Minio
from urllib3 import PoolManager
from urllib3.util import Timeout

config = json.load(sys.stdin)
token = config["token"]
if not re.fullmatch(r"[0-9a-f]{32}", token):
    raise SystemExit("Invalid probe identifier")
table = "_deployment_probe_" + token
bucket = "deployment-probe-" + token
payload = ("classic-bites-persistence-" + token).encode()
connection = pymysql.connect(
    host="mysql", user=config["MYSQL_USER"], password=config["MYSQL_PASSWORD"],
    database=config["MYSQL_DATABASE"], charset="utf8mb4", autocommit=True,
    connect_timeout=5, read_timeout=10, write_timeout=10,
)
client = Minio("minio:9000", access_key=config["MINIO_ROOT_USER"],
               secret_key=config["MINIO_ROOT_PASSWORD"], secure=False,
               http_client=PoolManager(timeout=Timeout(connect=5, read=10), retries=0))
try:
    with connection.cursor() as cursor:
        if config["mode"] == "seed":
            cursor.execute(f"CREATE TABLE `{table}` (id INT PRIMARY KEY, content VARBINARY(128))")
            cursor.execute(f"INSERT INTO `{table}` VALUES (1, %s)", (payload,))
            client.make_bucket(bucket)
            client.put_object(bucket, "probe.txt", io.BytesIO(payload), len(payload))
        elif config["mode"] == "verify":
            cursor.execute(f"SELECT content FROM `{table}` WHERE id=1")
            if cursor.fetchone() != (payload,):
                raise RuntimeError("MySQL probe mismatch")
            response = client.get_object(bucket, "probe.txt")
            try:
                if response.read() != payload:
                    raise RuntimeError("MinIO probe mismatch")
            finally:
                response.close()
                response.release_conn()
        elif config["mode"] == "cleanup":
            cursor.execute(f"DROP TABLE IF EXISTS `{table}`")
            if client.bucket_exists(bucket):
                client.remove_object(bucket, "probe.txt")
                client.remove_bucket(bucket)
        else:
            raise ValueError("Unknown mode")
finally:
    connection.close()
print(config["mode"] + ": MySQL and MinIO probe passed")
