#!/usr/bin/env python3
"""Exercise the running media API and delete only objects this run creates.

Uses Python's standard library. Compose resolves credentials in memory; neither
credentials nor response bodies are printed. HTTP redirects are not followed.
"""

import argparse
import http.client
import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import unquote, urlencode, urlsplit
import uuid


ROOT = Path(__file__).resolve().parent.parent
CATEGORIES = ("images", "videos", "audio", "documents", "subtitles")


class CheckFailure(Exception):
    """An intentionally credential-free failure message."""


def require(condition, message):
    if not condition:
        raise CheckFailure(message)


def resolved_settings():
    try:
        result = subprocess.run(
            ["docker", "compose", "config", "--format", "json"],
            cwd=ROOT, text=True, capture_output=True, check=False, timeout=30,
        )
        require(result.returncode == 0, "Compose configuration could not be resolved")
        service = json.loads(result.stdout)["services"]["fastapi"]
        environment = service["environment"]
        api_key = environment["MEDIA_API_KEY"]
        require(bool(api_key), "MEDIA_API_KEY is missing; run ./stack.sh install first")
        port = next(port["published"] for port in service["ports"] if int(port["target"]) == 8000)
        maximum = int(environment.get("MAX_UPLOAD_BYTES", 100 * 1024 * 1024))
        return str(api_key), str(port), maximum
    except CheckFailure:
        raise
    except Exception:
        raise CheckFailure("Could not read API configuration from Docker Compose") from None


class API:
    def __init__(self, base_url, api_key, timeout):
        try:
            url = urlsplit(base_url)
            require(url.scheme in ("http", "https") and bool(url.hostname),
                    "Base URL must use http:// or https://")
            require(not url.username and not url.password and not url.query and not url.fragment,
                    "Base URL must not contain credentials, a query, or a fragment")
            require(url.path in ("", "/"), "Base URL must not contain a path")
            self.host, self.port, self.scheme = url.hostname, url.port, url.scheme
        except ValueError:
            raise CheckFailure("Base URL is invalid") from None
        self.api_key, self.timeout = api_key, timeout
        self.created = set()

    def request(self, method, path, *, query=None, body=None, headers=None, auth="valid"):
        if query:
            path += "?" + urlencode(query)
        request_headers = dict(headers or {})
        if auth == "valid":
            request_headers["X-API-Key"] = self.api_key
        elif auth == "wrong":
            request_headers["X-API-Key"] = self.api_key + "-incorrect"
        connection_type = http.client.HTTPSConnection if self.scheme == "https" else http.client.HTTPConnection
        connection = connection_type(self.host, self.port, timeout=self.timeout)
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            data = response.read(2 * 1024 * 1024 + 1)
            require(len(data) <= 2 * 1024 * 1024, "Unexpectedly large API response")
            return response.status, {name.lower(): value for name, value in response.getheaders()}, data
        except CheckFailure:
            raise
        except Exception:
            raise CheckFailure("API connection failed or timed out during " + method + " " + path.split("?")[0]) from None
        finally:
            connection.close()

    def expect(self, expected, method, path, **kwargs):
        status, headers, data = self.request(method, path, **kwargs)
        require(status == expected,
                method + " " + path + ": expected HTTP " + str(expected) + ", received HTTP " + str(status))
        return headers, data

    def json(self, expected, method, path, **kwargs):
        _, data = self.expect(expected, method, path, **kwargs)
        try:
            value = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            raise CheckFailure(method + " " + path + ": invalid JSON response") from None
        require(isinstance(value, dict), method + " " + path + ": expected a JSON object")
        return value

    def cleanup(self):
        failed = 0
        for key in list(self.created):
            try:
                status, _, _ = self.request("DELETE", "/files", query={"key": key})
                if status not in (204, 404):
                    failed += 1
                else:
                    self.created.discard(key)
            except Exception:
                failed += 1
        if failed:
            print("FAIL cleanup: " + str(failed) + " test object(s) remain", file=sys.stderr)
            # These are only keys returned by successful creates in this run.
            for key in sorted(self.created):
                print("Test object requiring cleanup: " + key, file=sys.stderr)
        return failed == 0


def multipart(payload, filename, category=None):
    boundary = "classic-bites-check-" + uuid.uuid4().hex
    parts = []
    if category is not None:
        parts.append(("--" + boundary + '\r\nContent-Disposition: form-data; name="category"\r\n\r\n'
                      + category + "\r\n").encode("utf-8"))
    parts.append(("--" + boundary + '\r\nContent-Disposition: form-data; name="file"; filename="'
                  + filename + '"\r\nContent-Type: application/octet-stream\r\n\r\n').encode("utf-8"))
    parts.extend((payload, ("\r\n--" + boundary + "--\r\n").encode("ascii")))
    return b"".join(parts), {"Content-Type": "multipart/form-data; boundary=" + boundary}


def assert_metadata(info, key, payload, filename):
    require(info.get("key") == key, "Object metadata returned an unexpected key")
    require(info.get("size") == len(payload), "Object size does not match uploaded bytes")
    require(info.get("filename") == filename, "Unicode filename was not preserved")
    require(info.get("content_type") == "application/octet-stream", "Object content type was not preserved")
    require(bool(info.get("bucket")) and bool(info.get("etag")) and bool(info.get("last_modified")),
            "Object metadata is incomplete")


def check(api, maximum):
    token = uuid.uuid4().hex
    missing = "images/" + token + "-missing.bin"
    huge_headers = {"Content-Type": "multipart/form-data; boundary=check",
                    "Content-Length": str(maximum + 1024 * 1024 + 1)}
    for auth in ("missing", "wrong"):
        for method, path in (("GET", "/files"), ("GET", "/files/info"),
                             ("GET", "/files/download"), ("DELETE", "/files")):
            api.expect(401, method, path, query={"key": missing}, auth=auth)
        api.expect(401, "POST", "/files", headers=huge_headers, auth=auth)
        api.expect(401, "PUT", "/files", query={"key": missing}, headers=huge_headers, auth=auth)
    api.expect(413, "POST", "/files", headers=huge_headers)
    api.expect(413, "PUT", "/files", query={"key": missing}, headers=huge_headers)
    api.expect(200, "GET", "/health", auth="missing")
    print("PASS authentication before body parsing; oversized headers rejected without uploading data")

    filename = "검증 문서-" + token + ".bin"
    payload = b"\x00\xffclassic-bites\r\n" + "한글 테스트\n".encode("utf-8")
    objects = []
    # Three images with the same filename prove creation does not overwrite an
    # earlier upload and provide at least two pages after the smallest own key.
    for form_category in (*CATEGORIES, "images", None):
        category = form_category or "images"
        data, headers = multipart(payload, filename, form_category)
        info = api.json(201, "POST", "/files", body=data, headers=headers)
        key = info.get("key")
        require(isinstance(key, str) and key.startswith(category + "/") and not key.endswith("/"),
                "Create returned an invalid object key")
        require(key not in api.created, "Create reused a key and overwrote an earlier object")
        api.created.add(key)
        objects.append((category, key))
        assert_metadata(info, key, payload, filename)
        current = api.json(200, "GET", "/files/info", query={"key": key})
        assert_metadata(current, key, payload, filename)
        download_headers, downloaded = api.expect(200, "GET", "/files/download", query={"key": key})
        require(downloaded == payload, "Download changed binary or newline content")
        disposition = download_headers.get("content-disposition", "")
        require(disposition.lower().startswith("attachment;") and "\r" not in disposition and "\n" not in disposition,
                "Download lacks a safe attachment disposition")
        require("filename*=utf-8''" in disposition.lower() and filename in unquote(disposition),
                "Download disposition does not preserve the Unicode filename")
        require(download_headers.get("content-length") == str(len(payload)), "Download Content-Length is incorrect")
        require(download_headers.get("x-content-type-options") == "nosniff", "Download lacks nosniff protection")
    print("PASS create, metadata, exact binary download, and Unicode filenames for all five categories")
    print("PASS repeated filenames create distinct objects")

    for query in ({}, {"category": "images", "limit": 1000}):
        listing = api.json(200, "GET", "/files", query=query)
        require(isinstance(listing.get("items"), list) and "next_start_after" in listing,
                "List response does not match the pagination contract")
        for item in listing["items"]:
            require(not item["key"].endswith("/"), "List exposed a folder marker")
            if query.get("category"):
                require(item["key"].startswith("images/"), "List category filter returned another category")
    images = sorted(key for category, key in objects if category == "images")
    cursor = images[0]
    first = api.json(200, "GET", "/files", query={"category": "images", "limit": 1, "start_after": cursor})
    require(len(first.get("items", [])) == 1, "Pagination first page did not honor limit=1")
    first_key = first["items"][0]["key"]
    require(first_key > cursor and first_key.startswith("images/") and not first_key.endswith("/"),
            "Pagination did not advance within the requested category")
    require(first.get("next_start_after") == first_key, "Pagination omitted or changed the continuation cursor")
    second = api.json(200, "GET", "/files", query={"category": "images", "limit": 1, "start_after": first_key})
    require(len(second.get("items", [])) == 1 and second["items"][0]["key"] > first_key,
            "Pagination repeated or skipped the required second page")
    print("PASS category filters, marker exclusion, and cursor pagination with existing bucket contents")

    replace_key = objects[0][1]
    replacement = b"replacement\x00\x01\xfe\n"
    replacement_filename = "교체 문서-" + token + ".bin"
    body, headers = multipart(replacement, replacement_filename)
    updated = api.json(200, "PUT", "/files", query={"key": replace_key}, body=body, headers=headers)
    assert_metadata(updated, replace_key, replacement, replacement_filename)
    assert_metadata(api.json(200, "GET", "/files/info", query={"key": replace_key}),
                    replace_key, replacement, replacement_filename)
    _, downloaded = api.expect(200, "GET", "/files/download", query={"key": replace_key})
    require(downloaded == replacement, "Replacement bytes were not persisted")
    for category, key in objects[1:]:
        _, untouched = api.expect(200, "GET", "/files/download", query={"key": key})
        require(untouched == payload, "Replacement modified a different object")
    print("PASS replacement preserves the key and leaves other created objects unchanged")

    invalid_keys = ("images/", "images/../" + token, "images/./" + token,
                    "/images/" + token, "images/" + token + "\\file", "unknown/" + token,
                    "images/" + token + "\x00", "images/" + "x" * 1024)
    for key in invalid_keys:
        for method, path in (("GET", "/files/info"), ("GET", "/files/download"), ("DELETE", "/files")):
            api.expect(422, method, path, query={"key": key})
        api.expect(422, "PUT", "/files", query={"key": key}, body=body, headers=headers)
    for query in ({"category": "unknown"}, {"limit": 0}, {"limit": 1001},
                  {"category": "images", "start_after": objects[1][1]}, {"start_after": "images/"}):
        api.expect(422, "GET", "/files", query=query)
    invalid_body, invalid_headers = multipart(payload, filename, "unknown")
    api.expect(422, "POST", "/files", body=invalid_body, headers=invalid_headers)
    for method, path in (("GET", "/files/info"), ("GET", "/files/download"), ("DELETE", "/files")):
        api.expect(404, method, path, query={"key": missing})
    api.expect(404, "PUT", "/files", query={"key": missing}, body=body, headers=headers)
    print("PASS invalid keys, folder marker protection, invalid categories, list bounds, and missing-object responses")

    for _, key in objects:
        _, deleted = api.expect(204, "DELETE", "/files", query={"key": key})
        require(not deleted, "Delete HTTP 204 unexpectedly returned a body")
        api.created.discard(key)
        api.expect(404, "GET", "/files/info", query={"key": key})
        api.expect(404, "GET", "/files/download", query={"key": key})
        api.expect(404, "DELETE", "/files", query={"key": key})
    print("PASS delete and subsequent HTTP 404; all objects created by this run removed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", help="API origin; default is the local published FastAPI port. The API key is sent only to this origin.")
    parser.add_argument("--timeout", type=float, default=10, help="Per-request timeout in seconds (default: 10)")
    args = parser.parse_args()
    api = None
    success = False
    try:
        require(args.timeout > 0, "Timeout must be positive")
        api_key, port, maximum = resolved_settings()
        api = API(args.base_url or "http://127.0.0.1:" + port, api_key, args.timeout)
        check(api, maximum)
        success = True
    except CheckFailure as exc:
        print("FAIL " + str(exc), file=sys.stderr)
    except KeyboardInterrupt:
        print("FAIL interrupted; cleaning up created test objects", file=sys.stderr)
    except Exception:
        print("FAIL unexpected check error; response details withheld to protect credentials", file=sys.stderr)
    finally:
        if api is not None and not api.cleanup():
            success = False
    if success:
        print("PASS media API integration checks complete")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
