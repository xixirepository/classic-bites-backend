#!/usr/bin/env python3
"""Run isolated upload/resource tests using the API image's installed packages.

From the stack directory:
  docker compose exec -T fastapi python < scripts/check-media-limits.py

The ASGI app uses in-memory fake storage and a 32-byte upload limit. No network,
real credentials, real objects, or large uploads are used. The harness itself
requires only the standard library; it imports the application's dependencies.
"""

import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlencode


source = Path(__file__).resolve().parent.parent / "api"
if (source / "main.py").is_file():
    sys.path.insert(0, str(source))

from main import Settings, create_app  # noqa: E402
from minio.error import S3Error  # noqa: E402
from starlette.requests import ClientDisconnect  # noqa: E402


API_KEY = "isolated-test-key"
FILE_LIMIT = 32
BOUNDARY = "ClassicBitesTestBoundary"
CONTENT_TYPE = "multipart/form-data; boundary=" + BOUNDARY


def multipart(payload=b"small file", filename="test.bin"):
    return (
        ("--" + BOUNDARY + '\r\nContent-Disposition: form-data; name="file"; filename="'
         + filename + '"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()
        + payload + ("\r\n--" + BOUNDARY + "--\r\n").encode()
    )


class FakeDownload:
    def __init__(self, item):
        self.data = item["data"]
        self.headers = {**item["metadata"], "Content-Length": str(len(self.data)),
                        "Content-Type": item["content_type"]}
        self.closed = False
        self.released = False
        self.iterated = False

    def stream(self, amt):
        self.iterated = True
        # More than one chunk makes early client disconnects observable.
        step = max(1, min(amt, 3))
        for position in range(0, len(self.data), step):
            yield self.data[position:position + step]

    def close(self):
        self.closed = True

    def release_conn(self):
        self.released = True


class FakeStorage:
    def __init__(self):
        self.objects = {}
        self.put_count = 0
        self.downloads = []

    def put_object(self, bucket, key, stream, size, *, content_type, metadata, num_parallel_uploads):
        data = stream.read()
        if len(data) != size:
            raise AssertionError("Storage received the wrong file size")
        self.put_count += 1
        self.objects[key] = {"data": data, "content_type": content_type,
                             "metadata": {"x-amz-meta-" + name: value for name, value in metadata.items()}}

    def stat_object(self, bucket, key):
        if key not in self.objects:
            raise S3Error("NoSuchKey", "not found", None, None, None, None)
        item = self.objects[key]
        return SimpleNamespace(size=len(item["data"]), content_type=item["content_type"],
                               metadata=item["metadata"], etag=hashlib.sha256(item["data"]).hexdigest(),
                               last_modified=datetime.now(timezone.utc))

    def get_object(self, bucket, key):
        self.stat_object(bucket, key)
        result = FakeDownload(self.objects[key])
        self.downloads.append(result)
        return result


class MediaResourceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.storage = FakeStorage()
        self.app = create_app(
            settings=Settings(api_key=API_KEY, access_key="test-access", secret_key="test-secret",
                              max_upload_bytes=FILE_LIMIT),
            storage=self.storage,
        )
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.temporary_files = []
        original = tempfile.SpooledTemporaryFile

        def recorded_spool(*args, **kwargs):
            result = original(*args, **kwargs)
            self.temporary_files.append(result)
            return result

        self.spool_patch = patch("starlette.formparsers.SpooledTemporaryFile", recorded_spool)
        self.spool_patch.start()

    async def asyncTearDown(self):
        self.spool_patch.stop()
        await self.lifespan.__aexit__(None, None, None)
        for temporary_file in self.temporary_files:
            self.assertTrue(temporary_file.closed, "A multipart temporary file was not closed")
        for download in self.storage.downloads:
            self.assertTrue(download.closed, "A download body was not closed")
            self.assertTrue(download.released, "A download connection was not released")

    async def invoke(self, method="POST", path="/files", body=None, *, headers=None,
                     auth=API_KEY, query=None, receiver=None, sender=None, chunks=None):
        request_headers = [(b"content-type", CONTENT_TYPE.encode())]
        if auth is not None:
            request_headers.append((b"x-api-key", auth.encode()))
        request_headers.extend(headers or [])
        if body is None:
            body = multipart()
        pending = list(chunks) if chunks is not None else [body]
        messages = []
        received = 0

        async def receive():
            nonlocal received
            received += 1
            if receiver is not None:
                return await receiver()
            if pending:
                chunk = pending.pop(0)
                return {"type": "http.request", "body": chunk, "more_body": bool(pending)}
            return {"type": "http.disconnect"}

        async def send(message):
            messages.append(message)
            if sender is not None:
                await sender(message)

        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
                 "http_version": "1.1", "method": method, "scheme": "http", "path": path,
                 "raw_path": path.encode(), "query_string": urlencode(query or {}).encode(),
                 "root_path": "", "headers": request_headers,
                 "client": ("127.0.0.1", 12345), "server": ("test", 80)}
        await asyncio.wait_for(self.app(scope, receive, send), timeout=5)
        status = next(message["status"] for message in messages if message["type"] == "http.response.start")
        response_body = b"".join(message.get("body", b"") for message in messages
                                 if message["type"] == "http.response.body")
        return status, response_body, received

    async def assert_next_upload_succeeds(self):
        status, data, _ = await self.invoke()
        self.assertEqual(status, 201)
        return json.loads(data)["key"]

    async def test_authentication_rejects_before_receiving_any_body(self):
        for auth in (None, "incorrect"):
            status, _, received = await self.invoke(auth=auth,
                headers=[(b"content-length", str(FILE_LIMIT + 1024 * 1024 + 1).encode())])
            self.assertEqual(status, 401)
            self.assertEqual(received, 0)
        self.assertEqual(self.storage.put_count, 0)

    async def test_declared_length_limit_rejects_without_consuming_body(self):
        status, _, received = await self.invoke(
            headers=[(b"content-length", str(FILE_LIMIT + 1024 * 1024 + 1).encode())])
        self.assertEqual(status, 413)
        self.assertEqual(received, 0)
        self.assertEqual(self.storage.put_count, 0)
        await self.assert_next_upload_succeeds()

    async def test_actual_file_limit_without_content_length(self):
        data = multipart(b"x" * (FILE_LIMIT + 1))
        status, _, received = await self.invoke(chunks=[data[index:index + 11] for index in range(0, len(data), 11)])
        self.assertEqual(status, 413)
        self.assertGreater(received, 1)
        self.assertEqual(self.storage.put_count, 0)
        self.assertTrue(self.temporary_files)
        self.assertTrue(all(item.closed for item in self.temporary_files))
        await self.assert_next_upload_succeeds()

    async def test_exact_file_limit_is_accepted(self):
        status, _, _ = await self.invoke(body=multipart(b"x" * FILE_LIMIT))
        self.assertEqual(status, 201)
        self.assertEqual(self.storage.put_count, 1)

    async def test_actual_request_limit_without_content_length(self):
        # One MiB framing allowance is small enough to test in memory. The
        # middleware must count received bytes before the multipart parser.
        status, _, _ = await self.invoke(body=b"x" * (FILE_LIMIT + 1024 * 1024 + 1))
        self.assertEqual(status, 413)
        self.assertEqual(self.storage.put_count, 0)
        await self.assert_next_upload_succeeds()

    async def test_truncated_multipart_closes_files_and_releases_upload_slot(self):
        data = multipart(b"unfinished")
        data = data[:data.rfind(("\r\n--" + BOUNDARY).encode())]
        status, _, _ = await self.invoke(body=data)
        self.assertEqual(status, 400)
        self.assertTrue(self.temporary_files)
        self.assertTrue(all(item.closed for item in self.temporary_files))
        self.assertEqual(self.storage.put_count, 0)
        await self.assert_next_upload_succeeds()

    async def test_malformed_multipart_returns_client_error(self):
        status, _, _ = await self.invoke(body=b"invalid multipart boundary\r\n")
        self.assertEqual(status, 400)
        self.assertEqual(self.storage.put_count, 0)
        await self.assert_next_upload_succeeds()

    async def test_disconnect_closes_partial_file_and_releases_upload_slot(self):
        data = multipart(b"unfinished")
        data = data[:data.rfind(("\r\n--" + BOUNDARY).encode())]
        pending = [{"type": "http.request", "body": data, "more_body": True},
                   {"type": "http.disconnect"}]

        async def disconnecting_receive():
            return pending.pop(0)

        with self.assertRaises(ClientDisconnect):
            await self.invoke(receiver=disconnecting_receive)
        self.assertTrue(self.temporary_files)
        self.assertTrue(all(item.closed for item in self.temporary_files))
        self.assertEqual(self.storage.put_count, 0)
        await self.assert_next_upload_succeeds()

    async def test_concurrent_upload_is_rejected_without_waiting_or_reading(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_receive():
            started.set()
            await release.wait()
            return {"type": "http.request", "body": multipart(), "more_body": False}

        active = asyncio.create_task(self.invoke(receiver=slow_receive))
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            status, _, received = await self.invoke()
            self.assertEqual(status, 429)
            self.assertEqual(received, 0)
            self.assertEqual(self.storage.put_count, 0)
        finally:
            release.set()
            result = await active
        self.assertEqual(result[0], 201)
        await self.assert_next_upload_succeeds()

    async def test_download_disconnect_before_iteration_releases_connection(self):
        key = await self.assert_next_upload_succeeds()

        async def broken_send(message):
            if message["type"] == "http.response.start":
                raise OSError("simulated client disconnect")

        with self.assertRaises(Exception):
            await self.invoke("GET", "/files/download", body=b"", query={"key": key}, sender=broken_send)
        download = self.storage.downloads[-1]
        self.assertFalse(download.iterated)
        self.assertTrue(download.closed)
        self.assertTrue(download.released)

    async def test_download_disconnect_during_iteration_releases_connection(self):
        key = await self.assert_next_upload_succeeds()

        async def broken_send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                raise OSError("simulated client disconnect")

        with self.assertRaises(Exception):
            await self.invoke("GET", "/files/download", body=b"", query={"key": key}, sender=broken_send)
        download = self.storage.downloads[-1]
        self.assertTrue(download.iterated)
        self.assertTrue(download.closed)
        self.assertTrue(download.released)


if __name__ == "__main__":
    unittest.main(verbosity=2)
