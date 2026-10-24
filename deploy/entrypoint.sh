#!/bin/sh
set -eu

# NewsRepository initializes its local schema on open. Preserve the host input
# as read-only and work on a separate writable runtime copy instead.
news_input="${ALPHA_MVP_NEWS_INPUT:?news input path is required}"
news_runtime="${ALPHA_MVP_NEWS_PATH:?news runtime path is required}"
mkdir -p "$(dirname "$news_runtime")"
cp -- "$news_input" "$news_runtime"

exec "$@"
