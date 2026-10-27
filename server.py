    def _static(self, path):
        files = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/chatroom": ("chatroom.html", "text/html; charset=utf-8"),
            "/chatroom.html": ("chatroom.html", "text/html; charset=utf-8"),
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

        





        





        





        