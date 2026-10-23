# Nightwire

A private, invite-only chat room for five fixed accounts, including the `kyle` owner account. It supports direct messages, private group chats, live updates, and file attachments up to 10 MB. There is no registration or user-creation endpoint.

## Configure Supabase

1. In the Supabase dashboard, open **SQL Editor** and run [`supabase/schema.sql`](./supabase/schema.sql). This creates the app's tables, enables row-level security without public policies, and creates the private `chat-files` Storage bucket.
2. Copy `.env.example` to `.env` and fill in your Supabase project URL, server-only key, and the five account passwords. Keep `.env` private; it is ignored by Git. Do not put a Supabase secret/service-role key in `public/`, HTML, or browser JavaScript.
3. Start the app:

```sh
python3 server.py
```

Open <http://127.0.0.1:8000>. Python 3.10 or newer is required; the server uses only the Python standard library. On hosted deployments, configure the same values in the host's environment/secrets settings instead of committing `.env`.

Use `SUPABASE_SERVICE_ROLE_KEY` for a legacy service-role key, or `SUPABASE_SECRET_KEY` for a newer Supabase secret key. Either key is privileged and must only be available to server processes. The browser calls this app's API and never receives the Supabase key.

The server creates the fixed user records and direct-message channels on its first run. It hashes account passwords before storage; changing an environment password later does not reset an existing account password. To deploy over the internet, use HTTPS and set `CHAT_COOKIE_SECURE=1`.

Optional server settings:

| Variable | Default | Purpose |
| --- | --- | --- |
| `CHAT_HOST` | `127.0.0.1` | Bind address |
| `CHAT_PORT` | `8000` | HTTP port |
| `CHAT_COOKIE_SECURE` | `0` | Set to `1` when served over HTTPS |

## Access Supabase from PHP

[`database.php`](./database.php) provides a server-side Supabase REST client. PHP requires the cURL extension and the same `SUPABASE_URL` and server-only key in its environment.

```php
<?php
require_once __DIR__ . '/database.php';

$db = chatDatabase();
$users = $db->select('users', [
    'select' => 'username,display_name',
    'username' => 'eq.kyle',
]);
```

`select()` accepts PostgREST query parameters and `insert()` sends JSON rows. Never return the client key or use this helper from browser-delivered PHP output.
