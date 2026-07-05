# Persistent Speedrun/Anki sync server.
#
# Builds the fork's `anki-sync-server` crate (rslib/sync) from source so the
# server speaks the SAME protocol and understands the SAME sr_* tables as the
# desktop and Android clients (they all share the rslib engine). The desktop's
# bundled loopback server is only a test rig; this image is the persistent,
# network-reachable server a real deployment points both apps at.
#
# Build context = the repo root (the `anki/` directory):
#   docker build -f deploy/sync-server/Dockerfile -t speedrun-sync .
#
# Run (persist the collection/media on a mounted volume):
#   docker run -p 8080:8080 -v speedrun-sync-data:/data \
#     -e SYNC_USER1="student:CHANGE_ME" speedrun-sync

# ---- build stage ----
FROM rust:1.92-bookworm AS build
RUN apt-get update && apt-get install -y --no-install-recommends protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY . .
ENV PROTOC=/usr/bin/protoc
# Build only the sync server binary (pulls in rslib as a dependency).
RUN cargo build --release -p anki-sync-server

# ---- runtime stage ----
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /src/target/release/anki-sync-server /usr/local/bin/anki-sync-server

# SYNC_BASE holds the server-side collection + media; mount a volume here so it
# survives restarts/redeploys (SQLite on a persistent disk, NOT a network FS).
ENV SYNC_HOST=0.0.0.0 \
    SYNC_PORT=8080 \
    SYNC_BASE=/data
VOLUME ["/data"]
EXPOSE 8080

# SYNC_USER1 (format "user:password") MUST be provided at runtime as a secret;
# it is intentionally not baked into the image.
CMD ["anki-sync-server"]
