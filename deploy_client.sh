#!/usr/bin/env bash
# Wrapper — canonical script lives in scripts/deploy_client.sh
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scripts/deploy_client.sh" "$@"
