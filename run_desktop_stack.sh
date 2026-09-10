#!/usr/bin/env bash
# Wrapper — canonical script lives in scripts/run_desktop_stack.sh
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scripts/run_desktop_stack.sh" "$@"
