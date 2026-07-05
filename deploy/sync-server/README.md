# Hosting the Speedrun sync server

The desktop app can host a bundled `anki-sync-server` on `127.0.0.1` for a
USB/LAN test rig, but real use needs ONE persistent, network-reachable server
that the desktop and phone both point at. This directory deploys exactly that.

## Why not Firebase / a managed DB

Anki sync is a bespoke client<->server protocol with server-side merge, not a
CRUD database. Firestore/RTDB/Storage cannot be the sync server. You must run the
`anki-sync-server` binary (built here from the fork, so it understands the `sr_*`
Speedrun tables). It keeps a SQLite collection + media on a local disk, so it
needs a host with a persistent volume and a single running instance (SQLite over
a network filesystem corrupts).

## What it is

- `Dockerfile` - multi-stage build of the fork's `anki-sync-server` crate
  (`rslib/sync`), producing a slim runtime image.
- `fly.toml` - a Fly.io app with a persistent volume at `/data`.

Config (env vars): `SYNC_HOST`, `SYNC_PORT`, `SYNC_BASE` (data dir), and
`SYNC_USER1` = `"user:password"` (provide as a secret; never commit it).

## Deploy on Fly.io

```bash
# from the repo root (build context must be the repo root)
cd deploy/sync-server
fly launch --no-deploy --copy-config --name speedrun-sync
fly volumes create speedrun_sync_data --size 3 --region iad
fly secrets set SYNC_USER1="student:$(openssl rand -hex 16)"
cd ../.. && fly deploy --config deploy/sync-server/fly.toml --dockerfile deploy/sync-server/Dockerfile
```

Server URL: `https://speedrun-sync.fly.dev`.

## Deploy on Railway / a plain VM

- Railway: "Deploy from Dockerfile", add a Volume mounted at `/data`, set the
  `SYNC_USER1` variable, expose port 8080.
- VM (Docker): `docker build -f deploy/sync-server/Dockerfile -t speedrun-sync .`
  then `docker run -d -p 443:8080 -v speedrun-sync-data:/data -e SYNC_USER1="student:PASS" speedrun-sync`
  behind a TLS reverse proxy (Caddy/nginx).

## Point the apps at it

- Desktop: Preferences -> Syncing, set the sync server URL to your host, or use
  the "Sync with phone" QR (it encodes URL + user + token).
- Phone: Settings -> Sync -> enter the URL + user/password, or scan the QR.

Both apps then sync through the shared engine to one server, outside the USB test
setup. Sync is request-based ("Sync now"); real-time push is a future bonus.

## Not done here

The image is defined and ready, but this repo does not deploy it - that needs
your Fly.io/Railway account (or a VM) and a chosen password. Run the commands
above to bring it up.
