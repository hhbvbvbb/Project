#!/usr/bin/env python3
import base64
import hashlib
import json
import os
import re
import secrets
import shlex
import sys
import time
import uuid
from email import policy
from email.parser import BytesParser
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
PUBLIC_DIR = ROOT / "public"
OWNER_USERNAME = "kyle"
USERS = {}
PASSWORD_ENV = {}
PASSWORD_ITERATIONS = 310_000
SESSION_SECONDS = 60 * 60 * 24 * 7
MAX_JSON_BYTES = 64 * 1024
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_MESSAGE_LENGTH = 2000
STORAGE_BUCKET = "chat-files"


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class SupabaseError(Exception):
    pass


def load_local_environment():
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return
    for line_number, line in enumerate(env_path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, separator, value = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name.strip()):
            raise RuntimeError(f"Invalid assignment in .env on line {line_number}")
        name = name.strip()
        if name in os.environ:
            continue
        try:
            parts = shlex.split(value, comments=True)
        except ValueError as exc:
            raise RuntimeError(f"Invalid value in .env on line {line_number}") from exc
        os.environ[name] = " ".join(parts)


def load_user_registry():
    global USERS, PASSWORD_ENV

    configured = {}
    for key, raw_value in os.environ.items():
        name = key.strip().upper()
        value = raw_value.strip()
        if not value:
            continue

        if name.startswith("CHAT_USER_"):
            if name.endswith("_PASSWORD"):
                username = name[len("CHAT_USER_") : -len("_PASSWORD")].lower()
                configured.setdefault(username, {})["password"] = value
            elif name.endswith("_NAME"):
                username = name[len("CHAT_USER_") : -len("_NAME")].lower()
                configured.setdefault(username, {})["display_name"] = value
        elif name.startswith("CHAT_PASSWORD_"):
            username = name[len("CHAT_PASSWORD_") :].lower()
            configured.setdefault(username, {})["password"] = value

    if not configured:
        raise RuntimeError(
            "Set CHAT_USER_KYLE_NAME and CHAT_USER_KYLE_PASSWORD in the environment or .env file. "
            "Add more users with CHAT_USER_<USER>_NAME and CHAT_USER_<USER>_PASSWORD."
        )

    owner_entry = configured.get(OWNER_USERNAME)
    if owner_entry is None or not owner_entry.get("password"):
        raise RuntimeError(
            "The owner account is required. Set CHAT_USER_KYLE_NAME and CHAT_USER_KYLE_PASSWORD."
        )

    USERS.clear()
    PASSWORD_ENV.clear()
    for username in sorted(configured):
        details = configured[username]
        password = details.get("password", "").strip()
        if not password or len(password) < 8:
            raise RuntimeError(
                f"User '{username}' must have a password of at least 8 characters."
            )

        display_name = details.get("display_name") or username.replace("_", " ").title()
        if username == OWNER_USERNAME and not details.get("display_name"):
            display_name = "Kyle"

        USERS[username] = display_name
        PASSWORD_ENV[username] = password

    return USERS, PASSWORD_ENV


class SupabaseClient:
    def __init__(self, project_url, api_key):
        self.project_url = project_url.rstrip("/")
        self.api_key = api_key

    @classmethod
    def from_environment(cls):
        project_url = os.environ.get("SUPABASE_URL", "").strip()
        api_key = (
            os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
            or os.environ.get("SUPABASE_SECRET_KEY", "").strip()
        )
        if not project_url or not api_key:
            raise RuntimeError(
                "Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY "
                "(or SUPABASE_SECRET_KEY) in the environment or root .env file."
            )
        parsed_url = urlparse(project_url)
        local_http = parsed_url.scheme == "http" and parsed_url.hostname in {
            "localhost",
            "127.0.0.1",
        }
        if parsed_url.scheme != "https" and not local_http:
            raise RuntimeError("SUPABASE_URL must use HTTPS.")
        return cls(project_url, api_key)

    def request(self, method, path, *, query=None, payload=None, raw=None, content_type=None, prefer=None):
        url = f"{self.project_url}{path}"
        if query:
            url += "?" + urlencode(query)
        body = raw
        headers = {"apikey": self.api_key, "Accept": "application/json"}
        if not self.api_key.startswith("sb_secret_"):
            headers["Authorization"] = f"Bearer {self.api_key}"
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif content_type:
            headers["Content-Type"] = content_type
        if prefer:
            headers["Prefer"] = prefer
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=15) as response:
                result = response.read()
        except HTTPError as exc:
            details = exc.read(4096).decode("utf-8", "replace")
            raise SupabaseError(f"Supabase returned HTTP {exc.code}: {details}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise SupabaseError(f"Could not reach Supabase: {exc}") from exc
        if not result:
            return None
        if content_type and content_type != "application/json":
            return result
        try:
            return json.loads(result)
        except json.JSONDecodeError as exc:
            raise SupabaseError("Supabase returned an invalid JSON response.") from exc

    def select(self, table, columns="*", **filters):
        query = {"select": columns, **filters}
        result = self.request("GET", f"/rest/v1/{table}", query=query)
        if not isinstance(result, list):
            raise SupabaseError(f"Supabase returned an invalid response for {table}.")
        return result

    def insert(self, table, rows):
        result = self.request(
            "POST",
            f"/rest/v1/{table}",
            payload=rows,
            prefer="return=representation",
        )
        if not isinstance(result, list):
            raise SupabaseError(f"Supabase did not return inserted {table} rows.")
        return result

    def upsert(self, table, rows, conflict):
        result = self.request(
            "POST",
            f"/rest/v1/{table}",
            query={"on_conflict": conflict},
            payload=rows,
            prefer="resolution=merge-duplicates,return=representation",
        )
        if not isinstance(result, list):
            raise SupabaseError(f"Supabase did not return upserted {table} rows.")
        return result

    def delete(self, table, **filters):
        self.request("DELETE", f"/rest/v1/{table}", query=filters)

    def upload(self, path, data):
        return self.request(
            "POST",
            f"/storage/v1/object/{STORAGE_BUCKET}/{quote(path, safe='/')}",
            raw=data,
            content_type="application/octet-stream",
        )

    def download(self, path):
        return self.request(
            "GET",
            f"/storage/v1/object/{STORAGE_BUCKET}/{quote(path, safe='/')}",
            content_type="application/octet-stream",
        )

    def remove_storage_object(self, path):
        self.request(
            "DELETE",
            f"/storage/v1/object/{STORAGE_BUCKET}",
            payload={"prefixes": [path]},
        )


def password_record(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )
    return salt, digest


def initialize_database(db, passwords):
    stored_users = db.select("users", "username,salt,password_hash")
    known_users = {row["username"] for row in stored_users}
    new_users = []
    for username, display_name in USERS.items():
        if username not in known_users:
            salt, digest = password_record(passwords[username])
            new_users.append(
                {
                    "username": username,
                    "display_name": display_name,
                    "salt": base64.b64encode(salt).decode("ascii"),
                    "password_hash": base64.b64encode(digest).decode("ascii"),
                }
            )
    if new_users:
        db.upsert("users", new_users, "username")

    now = int(time.time() * 1000)
    usernames = list(USERS)
    conversations = []
    memberships = []
    for index, first in enumerate(usernames):
        for second in usernames[index + 1 :]:
            conversation_id = f"dm:{first}:{second}"
            conversations.append(
                {
                    "id": conversation_id,
                    "kind": "dm",
                    "name": None,
                    "created_by": first,
                    "created_at": now,
                }
            )
            memberships.extend(
                (
                    {"conversation_id": conversation_id, "username": first},
                    {"conversation_id": conversation_id, "username": second},
                )
            )
    db.upsert("conversations", conversations, "id")
    db.upsert("conversation_members", memberships, "conversation_id,username")


class ChatHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, database, cookie_secure):
        super().__init__(address, handler)
        self.database = database
        self.cookie_secure = cookie_secure


class ChatHandler(BaseHTTPRequestHandler):
    server_version = "Nightwire/1.0"
    sys_version = ""

    def log_message(self, format, *args):
        sys.stderr.write(f"{self.address_string()} - {format % args}\n")

    def _db(self):
        return self.server.database

    def _headers(self, content_type=None):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        )
        self.send_header("Cache-Control", "no-store")
        if content_type:
            self.send_header("Content-Type", content_type)

    def _send_json(self, status, value, extra_headers=()):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._headers("application/json; charset=utf-8")
        for key, value in extra_headers:
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, status, body, content_type):
        self.send_response(status)
        self._headers(content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status, message):
        self._send_json(status, {"error": message})

    def _handle_database_error(self, exc):
        print(f"Supabase request failed: {exc}", file=sys.stderr)
        self._error(HTTPStatus.BAD_GATEWAY, "Database service unavailable")

    def _read_body(self, limit):
        length_header = self.headers.get("Content-Length")
        if length_header is None:
            raise ApiError(HTTPStatus.LENGTH_REQUIRED, "Content-Length is required")
        try:
            length = int(length_header)
        except ValueError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Invalid Content-Length") from exc
        if length < 0 or length > limit:
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request is too large")
        body = self.rfile.read(length)
        if len(body) != length:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Incomplete request body")
        return body

    def _read_json(self):
        if self.headers.get_content_type() != "application/json":
            raise ApiError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Expected JSON")
        try:
            value = json.loads(self._read_body(MAX_JSON_BYTES))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Invalid JSON") from exc
        if not isinstance(value, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "Expected a JSON object")
        return value

    def _check_origin(self):
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc != self.headers.get("Host"):
            raise ApiError(HTTPStatus.FORBIDDEN, "Cross-origin request denied")

    def _current_user(self, db):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception as exc:
            raise ApiError(HTTPStatus.UNAUTHORIZED, "Sign in required") from exc
        morsel = cookie.get("nightwire_session")
        if morsel is None:
            raise ApiError(HTTPStatus.UNAUTHORIZED, "Sign in required")
        token_hash = hashlib.sha256(morsel.value.encode("ascii", "ignore")).hexdigest()
        rows = db.select(
            "sessions",
            "username",
            token_hash=f"eq.{token_hash}",
            expires_at=f"gt.{int(time.time())}",
            limit="1",
        )
        if not rows or rows[0]["username"] not in USERS:
            raise ApiError(HTTPStatus.UNAUTHORIZED, "Sign in required")
        username = rows[0]["username"]
        return {"username": username, "display_name": USERS[username]}

    def _require_member(self, db, conversation_id, username):
        rows = db.select(
            "conversation_members",
            "username",
            conversation_id=f"eq.{conversation_id}",
            username=f"eq.{username}",
            limit="1",
        )
        if not rows:
            raise ApiError(HTTPStatus.NOT_FOUND, "Conversation not found")

    def _static(self, path):
        files = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/styles.css": ("styles.css", "text/css; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
        }
        entry = files.get(path)
        if entry is None:
            self._error(HTTPStatus.NOT_FOUND, "Not found")
            return
        name, content_type = entry
        self._send_file(HTTPStatus.OK, (PUBLIC_DIR / name).read_bytes(), content_type)

    def do_GET(self):
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            self._static(parsed.path)
            return
        try:
            db = self._db()
            if parsed.path == "/api/me":
                user = self._current_user(db)
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "user": {
                            "id": user["username"],
                            "displayName": user["display_name"],
                            "role": "owner" if user["username"] == OWNER_USERNAME else "member",
                        },
                        "users": [
                            {"id": username, "displayName": name}
                            for username, name in USERS.items()
                        ],
                    },
                )
            elif parsed.path == "/api/conversations":
                user = self._current_user(db)
                self._list_conversations(db, user["username"])
            elif parsed.path == "/api/messages":
                user = self._current_user(db)
                params = parse_qs(parsed.query)
                conversation_id = params.get("conversation", [""])[0]
                try:
                    after = max(0, int(params.get("after", ["0"])[0]))
                except ValueError as exc:
                    raise ApiError(HTTPStatus.BAD_REQUEST, "Invalid message cursor") from exc
                self._list_messages(db, user["username"], conversation_id, after)
            elif parsed.path.startswith("/api/files/"):
                user = self._current_user(db)
                self._get_file(
                    db, user["username"], parsed.path.removeprefix("/api/files/")
                )
            else:
                self._error(HTTPStatus.NOT_FOUND, "Not found")
        except ApiError as exc:
            self._error(exc.status, exc.message)
        except SupabaseError as exc:
            self._handle_database_error(exc)

    def _list_conversations(self, db, username):
        memberships = db.select(
            "conversation_members", "conversation_id", username=f"eq.{username}"
        )
        conversation_ids = [row["conversation_id"] for row in memberships]
        if not conversation_ids:
            self._send_json(HTTPStatus.OK, {"conversations": []})
            return
        conversations = db.select("conversations")
        members = db.select("conversation_members")
        users = {username: name for username, name in USERS.items()}
        messages = db.select(
            "messages",
            "conversation_id,body,created_at",
            order="id.desc",
            limit="1000",
        )
        last_message = {}
        for message in messages:
            last_message.setdefault(message["conversation_id"], message)
        entries = []
        for conversation in conversations:
            conversation_id = conversation["id"]
            if conversation_id not in conversation_ids:
                continue
            conversation_members = [
                {"id": row["username"], "displayName": users[row["username"]]}
                for row in members
                if row["conversation_id"] == conversation_id
                and row["username"] in users
            ]
            entry = {
                "id": conversation_id,
                "kind": conversation["kind"],
                "name": conversation["name"],
                "members": conversation_members,
                "lastMessage": None,
                "lastAt": None,
            }
            if conversation_id in last_message:
                entry["lastMessage"] = last_message[conversation_id]["body"]
                entry["lastAt"] = last_message[conversation_id]["created_at"]
            if entry["kind"] == "dm":
                entry["name"] = next(
                    member["displayName"]
                    for member in conversation_members
                    if member["id"] != username
                )
            entries.append(entry)
        entries.sort(key=lambda item: item["lastAt"] or 0, reverse=True)
        self._send_json(HTTPStatus.OK, {"conversations": entries})

    def _list_messages(self, db, username, conversation_id, after):
        self._require_member(db, conversation_id, username)
        query = {
            "select": "id,username,body,created_at",
            "conversation_id": f"eq.{conversation_id}",
            "order": "id.asc" if after else "id.desc",
            "limit": "100",
        }
        if after:
            query["id"] = f"gt.{after}"
        rows = db.request("GET", "/rest/v1/messages", query=query)
        if not isinstance(rows, list):
            raise SupabaseError("Supabase returned an invalid messages response.")
        if not after:
            rows.reverse()
        ids = [str(row["id"]) for row in rows]
        attachments = []
        if ids:
            attachments = db.select(
                "attachments",
                "id,message_id,name,size",
                message_id=f"in.({','.join(ids)})",
            )
        by_message = {str(row["message_id"]): row for row in attachments}
        self._send_json(
            HTTPStatus.OK,
            {
                "messages": [
                    {
                        "id": row["id"],
                        "userId": row["username"],
                        "author": USERS.get(row["username"], "Unknown user"),
                        "text": row["body"],
                        "createdAt": row["created_at"],
                        "file": (
                            {
                                "id": by_message[str(row["id"])]["id"],
                                "name": by_message[str(row["id"])]["name"],
                                "size": by_message[str(row["id"])]["size"],
                            }
                            if str(row["id"]) in by_message
                            else None
                        ),
                    }
                    for row in rows
                ]
            },
        )

    def _get_file(self, db, username, file_id):
        if not file_id.isdigit():
            raise ApiError(HTTPStatus.NOT_FOUND, "File not found")
        files = db.select(
            "attachments",
            "id,message_id,name,size,storage_path",
            id=f"eq.{file_id}",
            limit="1",
        )
        if not files:
            raise ApiError(HTTPStatus.NOT_FOUND, "File not found")
        attachment = files[0]
        messages = db.select(
            "messages",
            "conversation_id",
            id=f"eq.{attachment['message_id']}",
            limit="1",
        )
        if not messages:
            raise ApiError(HTTPStatus.NOT_FOUND, "File not found")
        self._require_member(db, messages[0]["conversation_id"], username)
        data = db.download(attachment["storage_path"])
        name = attachment["name"].replace("\r", "").replace("\n", "")
        self.send_response(HTTPStatus.OK)
        self._headers("application/octet-stream")
        self.send_header(
            "Content-Disposition",
            f"attachment; filename*=UTF-8''{quote(name, safe='')}",
        )
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        try:
            self._check_origin()
            parsed = urlparse(self.path)
            db = self._db()
            if parsed.path == "/api/login":
                self._login(db)
            elif parsed.path == "/api/logout":
                self._logout(db)
            else:
                user = self._current_user(db)
                if parsed.path == "/api/groups":
                    self._create_group(db, user["username"])
                elif parsed.path == "/api/messages":
                    self._create_message(db, user["username"])
                elif parsed.path == "/api/files":
                    self._create_file_message(db, user["username"])
                else:
                    self._error(HTTPStatus.NOT_FOUND, "Not found")
        except ApiError as exc:
            self._error(exc.status, exc.message)
        except SupabaseError as exc:
            self._handle_database_error(exc)

    def _login(self, db):
        body = self._read_json()
        username = body.get("username")
        password = body.get("password")
        if not isinstance(username, str) or not isinstance(password, str):
            raise ApiError(HTTPStatus.BAD_REQUEST, "Username and password are required")
        rows = db.select(
            "users", "salt,password_hash", username=f"eq.{username}", limit="1"
        )
        row = rows[0] if rows and username in USERS else None
        salt = base64.b64decode(row["salt"]) if row else bytes(16)
        expected = base64.b64decode(row["password_hash"]) if row else bytes(32)
        _, actual = password_record(password, salt)
        if row is None or not secrets.compare_digest(actual, expected):
            raise ApiError(HTTPStatus.UNAUTHORIZED, "Incorrect username or password")
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        db.insert(
            "sessions",
            [
                {
                    "token_hash": token_hash,
                    "username": username,
                    "expires_at": int(time.time()) + SESSION_SECONDS,
                }
            ],
        )
        secure = "; Secure" if self.server.cookie_secure else ""
        self._send_json(
            HTTPStatus.OK,
            {
                "user": {
                    "id": username,
                    "displayName": USERS[username],
                    "role": "owner" if username == OWNER_USERNAME else "member",
                },
                "users": [
                    {"id": user_id, "displayName": display_name}
                    for user_id, display_name in USERS.items()
                ],
            },
            [
                (
                    "Set-Cookie",
                    f"nightwire_session={token}; Path=/; HttpOnly; SameSite=Strict; "
                    f"Max-Age={SESSION_SECONDS}{secure}",
                )
            ],
        )

    def _logout(self, db):
        self._current_user(db)
        cookie = SimpleCookie()
        cookie.load(self.headers.get("Cookie", ""))
        token_hash = hashlib.sha256(
            cookie["nightwire_session"].value.encode("ascii", "ignore")
        ).hexdigest()
        db.delete("sessions", token_hash=f"eq.{token_hash}")
        secure = "; Secure" if self.server.cookie_secure else ""
        self._send_json(
            HTTPStatus.OK,
            {"ok": True},
            [
                (
                    "Set-Cookie",
                    f"nightwire_session=; Path=/; HttpOnly; SameSite=Strict; "
                    f"Max-Age=0{secure}",
                )
            ],
        )

    def _create_group(self, db, username):
        body = self._read_json()
        name = body.get("name")
        members = body.get("members")
        if not isinstance(name, str) or not isinstance(members, list):
            raise ApiError(HTTPStatus.BAD_REQUEST, "Group name and members are required")
        name = name.strip()
        if not name or len(name) > 40:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Group name must be 1–40 characters")
        if any(not isinstance(member, str) or member not in USERS for member in members):
            raise ApiError(HTTPStatus.BAD_REQUEST, "Invalid group member")
        members = list(dict.fromkeys([username, *members]))
        if len(members) < 2:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Choose at least one other member")
        conversation_id = f"group:{secrets.token_urlsafe(12)}"
        now = int(time.time() * 1000)
        db.insert(
            "conversations",
            [
                {
                    "id": conversation_id,
                    "kind": "group",
                    "name": name,
                    "created_by": username,
                    "created_at": now,
                }
            ],
        )
        db.insert(
            "conversation_members",
            [
                {"conversation_id": conversation_id, "username": member}
                for member in members
            ],
        )
        self._send_json(
            HTTPStatus.CREATED,
            {"id": conversation_id, "name": name, "kind": "group"},
        )

    def _create_message(self, db, username):
        body = self._read_json()
        conversation_id = body.get("conversationId")
        text = body.get("text")
        if not isinstance(conversation_id, str) or not isinstance(text, str):
            raise ApiError(HTTPStatus.BAD_REQUEST, "Conversation and message are required")
        text = text.strip()
        if not text or len(text) > MAX_MESSAGE_LENGTH:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Message must be 1–2000 characters")
        self._require_member(db, conversation_id, username)
        now = int(time.time() * 1000)
        rows = db.insert(
            "messages",
            [
                {
                    "conversation_id": conversation_id,
                    "username": username,
                    "body": text,
                    "created_at": now,
                }
            ],
        )
        self._send_json(
            HTTPStatus.CREATED, {"id": rows[0]["id"], "createdAt": now}
        )

    def _create_file_message(self, db, username):
        if self.headers.get_content_type() != "multipart/form-data":
            raise ApiError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Expected multipart form data")
        content_type = self.headers.get("Content-Type", "")
        if "boundary=" not in content_type:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Missing upload boundary")
        raw = self._read_body(MAX_UPLOAD_BYTES + 64 * 1024)
        message = BytesParser(policy=policy.default).parsebytes(
            b"Content-Type: "
            + content_type.encode("ascii")
            + b"\r\nMIME-Version: 1.0\r\n\r\n"
            + raw
        )
        fields = {}
        uploaded = None
        for part in message.iter_parts():
            field_name = part.get_param("name", header="content-disposition")
            if field_name == "file" and uploaded is None:
                uploaded = (part.get_filename(), part.get_payload(decode=True) or b"")
            elif field_name in {"conversationId", "text"}:
                fields[field_name] = (part.get_payload(decode=True) or b"").decode(
                    "utf-8", "strict"
                )
        if uploaded is None or not isinstance(fields.get("conversationId"), str):
            raise ApiError(HTTPStatus.BAD_REQUEST, "A conversation and file are required")
        original_name, data = uploaded
        if not original_name:
            raise ApiError(HTTPStatus.BAD_REQUEST, "File name is required")
        name = original_name.replace("\\", "/").split("/")[-1].strip()
        name = re.sub(r"[\x00-\x1f\x7f]", "", name)[:180]
        if not name:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Invalid file name")
        if not data or len(data) > MAX_UPLOAD_BYTES:
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "File must be under 10 MB")
        text = fields.get("text", "").strip()
        if len(text) > MAX_MESSAGE_LENGTH:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Caption must be 2000 characters or fewer")
        conversation_id = fields["conversationId"]
        self._require_member(db, conversation_id, username)

        storage_path = f"{uuid.uuid4().hex}/{name}"
        db.upload(storage_path, data)
        now = int(time.time() * 1000)
        message_rows = []
        try:
            message_rows = db.insert(
                "messages",
                [
                    {
                        "conversation_id": conversation_id,
                        "username": username,
                        "body": text,
                        "created_at": now,
                    }
                ],
            )
            db.insert(
                "attachments",
                [
                    {
                        "message_id": message_rows[0]["id"],
                        "name": name,
                        "size": len(data),
                        "storage_path": storage_path,
                    }
                ],
            )
        except SupabaseError:
            if message_rows:
                try:
                    db.delete("messages", id=f"eq.{message_rows[0]['id']}")
                except SupabaseError as cleanup_error:
                    print(f"Could not clean up failed file message: {cleanup_error}", file=sys.stderr)
            try:
                db.remove_storage_object(storage_path)
            except SupabaseError as cleanup_error:
                print(f"Could not clean up uploaded file: {cleanup_error}", file=sys.stderr)
            raise
        self._send_json(
            HTTPStatus.CREATED,
            {"id": message_rows[0]["id"], "createdAt": now},
        )


def main():
    load_local_environment()
    try:
        load_user_registry()
    except RuntimeError as exc:
        print(f"Nightwire startup failed: {exc}", file=sys.stderr)
        return 2

    try:
        database = SupabaseClient.from_environment()
        initialize_database(database, PASSWORD_ENV)
    except (RuntimeError, SupabaseError) as exc:
        print(f"Nightwire startup failed: {exc}", file=sys.stderr)
        return 1

    host = os.environ.get("CHAT_HOST", "127.0.0.1")
    port = int(os.environ.get("CHAT_PORT", "8000"))
    cookie_secure = os.environ.get("CHAT_COOKIE_SECURE", "0") == "1"
    server = ChatHTTPServer((host, port), ChatHandler, database, cookie_secure)
    print(f"Nightwire is running at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

