# RadioStudio Control REST API

The versioned HTTP API lets trusted clients on the local network read playback status and the current **Now Playing** queue, control playback, and reorder the queue. It runs separately from the audio streaming server.

## Enable and connect

Open **Settings → API**, enable the REST API, choose a port and an authentication mode, then save. The default port is `8091`; the audio server commonly uses `8090`. The API is disabled by default. When enabled, it listens on the computer's network interfaces so other LAN devices can connect. RadioStudio does not add system firewall rules.

All requests use the `/api/v1` prefix. The settings page lists connection URLs for the machine's detected IPv4 addresses. For example:

```text
http://192.168.1.25:8091/api/v1
```

Authentication is required for every endpoint. Choose one mode in Settings:

- **Generated token:** send `Authorization: Bearer <token>`.
- **Username and password:** send HTTP Basic authentication.

RadioStudio stores these credentials through `keyring`: Windows Credential Manager on Windows, or the graphical desktop session’s Secret Service (such as GNOME Keyring) on Ubuntu/Debian. The Linux credential service must be running and unlocked. It does not put them in the settings file or exported configuration backups.

> **HTTP is unencrypted.** Usernames, passwords, tokens, playback commands, and responses can be observed by someone with access to the network. Use the API only on a network you trust. The API does not provide TLS.

## Endpoints

### `GET /api/v1/status`

Returns playback state, progress, the current track's metadata, queue length, and the queue revision. `current_track` is `null` when no queue entry is current. Local file paths are never returned.

```json
{
  "playback_state": "playing",
  "position_ms": 42800,
  "duration_ms": 213000,
  "current_track": {
    "position": 0,
    "title": "Example Song",
    "artist": "Example Artist",
    "album": "Example Album",
    "is_current": true
  },
  "queue_length": 4,
  "queue_revision": 12
}
```

`playback_state` is `playing`, `paused`, or `stopped`. Progress values are milliseconds.

### `GET /api/v1/queue`

Returns every queue entry, the current entry, and a revision value. Positions are zero-based. Each item includes `title`, `artist`, `album`, and `is_current`; no local path is included.

```json
{
  "revision": 12,
  "current_track": {"position": 0, "title": "Example Song", "artist": "Example Artist", "album": "Example Album", "is_current": true},
  "items": [
    {"position": 0, "title": "Example Song", "artist": "Example Artist", "album": "Example Album", "is_current": true},
    {"position": 1, "title": "Next Song", "artist": "Another Artist", "album": "Another Album", "is_current": false}
  ]
}
```

### `POST /api/v1/playback`

Send a JSON object with one action:

```json
{"action":"play"}
```

Accepted actions are `play`, `pause`, `stop`, and `next`. `play` resumes a paused track; when stopped, it starts the current selection or the first queued track. `pause` has no effect if playback is not running. The response is the updated status.

### `PUT /api/v1/queue/order`

Send the revision from the latest `GET /api/v1/queue` response and a complete permutation of its zero-based positions. For example, `[1,0,2]` swaps the first two entries:

```json
{"revision":12,"order":[1,0,2,3]}
```

The response contains the new queue and revision. The currently playing occurrence stays in the queue and remains current after reordering. Duplicate files are valid queue entries; use their positions to distinguish occurrences.

If the queue has changed since it was read, or `order` is not a complete permutation, the API returns `409 Conflict`. Fetch the queue again before retrying.

## Examples

Replace `<token>` and the host with the values shown in Settings. `curl.exe` is included with current Windows versions:

```powershell
curl.exe -H "Authorization: Bearer <token>" `
  http://192.168.1.25:8091/api/v1/status

curl.exe -H "Authorization: Bearer <token>" `
  http://192.168.1.25:8091/api/v1/queue

curl.exe -X POST -H "Authorization: Bearer <token>" `
  -H "Content-Type: application/json" -d '{"action":"next"}' `
  http://192.168.1.25:8091/api/v1/playback
```

With username and password, use `curl.exe -u username:password` instead of the Bearer header. Avoid saving credentials in scripts or shell history.

## Browser dashboards and CORS

For a web dashboard, add each exact origin under **Allowed CORS origins**, such as `http://192.168.1.25:3000`. Enter only the scheme, host, and optional port. Wildcard origins are rejected. The API allows the `Authorization` and `Content-Type` request headers and handles preflight requests.

With no origins configured, native apps and scripts can connect, but browser cross-origin requests are not allowed. If a request includes an `Origin` header, it must match an allowlisted origin. CORS is a browser access policy; it is not authentication and does not replace the required API credentials.

## Errors

Errors use this JSON shape:

```json
{"error":{"code":"stale_queue_revision","message":"The queue changed. Read it again before reordering."}}
```

Common HTTP statuses:

- `400 Bad Request`: invalid JSON, missing revision, or unsupported playback action.
- `401 Unauthorized`: missing or incorrect credentials.
- `403 Forbidden`: request origin is not on the CORS allowlist.
- `404 Not Found`: unknown API path.
- `405 Method Not Allowed`: the HTTP method is not supported by that endpoint.
- `409 Conflict`: stale queue revision or invalid queue order.
- `411 Length Required`: a JSON request did not include `Content-Length`.
- `413 Content Too Large`: request body exceeds the size limit.
- `415 Unsupported Media Type`: a JSON endpoint was called without `application/json`.
- `503 Service Unavailable`: RadioStudio is shutting down.
- `504 Gateway Timeout`: the Qt application did not process the request in time.

The machine-readable [OpenAPI 3.0.3 document](rest_api.openapi.json) is also served at `GET /api/v1/openapi.json` and uses the same authentication requirement as the other endpoints.

## Scope of version 1

This API can read status and the current queue, reorder the existing queue, and control playback. It does not add or remove tracks, accept file paths, control transmission or AutoDJ, or trigger Cart Wall items.
