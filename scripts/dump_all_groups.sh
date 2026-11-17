#!/usr/bin/env bash
# Compatibility entry point; implementation lives in pipeline/.
ROOT=$(cd "$(dirname "$0")/.." && pwd)
exec bash "$ROOT/pipeline/dump_all_groups.sh" "$@"
