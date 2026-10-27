# Nightwire

A private, invite-only chat room for a configured set of fixed accounts, with the `kyle` owner account required. It supports direct messages, private group chats, live updates, and file attachments up to 10 MB.

## Configure Supabase

1. In the Supabase dashboard, open **SQL Editor** and run [`supabase/schema.sql`](./supabase/schema.sql). This creates the app's tables, enables row-level security without public policies, and creates the necessary storage bucket.
2. Copy `.env.example` to `.env` and fill in your Supabase project URL, server-only key, and your allowed chat users. The owner account (`kyle`) is required. Add more accounts by creating `CHAT_USER_<USER>_NAME` and `CHAT_USER_<USER>_PASSWORD` entries, or remove any block to disable that user. Keep `.env` private; it is ignored by Git. Do not put a Supabase secret/service key in frontend code.
3. Start the app:

```sh
python3 server.py
```

Open <http://127.0.0.1:8000>. Python 3.10 or newer is required; the server uses only the Python standard library. On hosted deployments, configure the same values in the host's environment/secrets manager.

Use `SUPABASE_SERVICE_ROLE_KEY` for a legacy service-role key, or `SUPABASE_SECRET_KEY` for a newer Supabase secret key. Either key is privileged and must only be available to server processes. The app hashes account passwords before storage; changing an environment password later does not reset an existing account's password hash unless you manually remove the stored record.

Optional server settings:

| Variable | Default | Purpose |
| --- | --- | --- |
| `CHAT_HOST` | `127.0.0.1` | Bind address |
| `CHAT_PORT` | `8000` | HTTP port |
| `CHAT_COOKIE_SECURE` | `0` | Set to `1` when served over HTTPS |

## Deploy the frontend to Netlify

The root `netlify.toml` sets the repository root as the base directory and publishes `public/` directly, without a build command. Keep this file at the repository root; no nested project directory is required.

This deploys the frontend assets only. Netlify static hosting does not run `server.py` or PHP, so login, messages, and uploads still require a separately hosted API or a backend adapted to Netlify.

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
