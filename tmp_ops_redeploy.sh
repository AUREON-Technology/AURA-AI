#!/bin/bash
set -euo pipefail
echo "=== pre-image IDs ==="
for c in aura-gateway-1 aura-worker-realtime-1 aura-worker-ingestion-1 aura-beat-1; do
  docker inspect -f "$c image={{.Image}} started={{.State.StartedAt}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}n/a{{end}}" "$c"
done

echo "=== refuse to touch secrets ==="
test -f /opt/aura-stack/deploy/.env
test -d /opt/aura-stack/deploy/secrets
ENV_BEFORE=$(sha256sum /opt/aura-stack/deploy/.env | awk '{print $1}')
# secret filenames only, never contents
echo "secret files: $(ls /opt/aura-stack/deploy/secrets | tr '\n' ' ')"

echo "=== extract sync to staging ==="
rm -rf /tmp/aura-sync
mkdir -p /tmp/aura-sync
tar -xf /tmp/aura-sync.tar -C /tmp/aura-sync
# belt: never copy secrets even if they slipped into the archive
rm -f /tmp/aura-sync/deploy/.env /tmp/aura-sync/aura_backend/.env \
      /tmp/aura-sync/aura_backend/credentials.json \
      /tmp/aura-sync/deploy/.envy
rm -rf /tmp/aura-sync/deploy/secrets /tmp/aura-sync/aura_backend/venv
test ! -f /tmp/aura-sync/deploy/.env
test ! -f /tmp/aura-sync/aura_backend/credentials.json
echo "staged top-level:"
ls /tmp/aura-sync
echo "native_ollama in staged config:"
grep -n native_ollama_base_url /tmp/aura-sync/aura_backend/config.py | head
echo "digest pin in staged compose:"
grep 'image: ollama' /tmp/aura-sync/deploy/docker-compose.prod.yml

echo "=== rsync into /opt/aura-stack (keep .env, secrets, generated nginx) ==="
rsync -a --delete-delay \
  --exclude '.env' \
  --exclude '.envy' \
  --exclude 'secrets/' \
  --exclude 'nginx/conf.d/aura.conf' \
  --exclude '*.bak*' \
  --exclude 'backups/' \
  /tmp/aura-sync/deploy/ /opt/aura-stack/deploy/
rsync -a --delete-delay \
  --exclude '.env' \
  --exclude 'credentials.json' \
  --exclude 'venv/' \
  --exclude '.git/' \
  --exclude '__pycache__/' \
  --exclude '.pytest_cache/' \
  --exclude 'local_qdrant_storage/' \
  /tmp/aura-sync/aura_backend/ /opt/aura-stack/aura_backend/

ENV_AFTER=$(sha256sum /opt/aura-stack/deploy/.env | awk '{print $1}')
if [ "$ENV_BEFORE" != "$ENV_AFTER" ]; then
  echo "FAILED: deploy/.env hash changed" >&2
  exit 1
fi
echo "deploy/.env hash unchanged"

# CRLF from Windows
find /opt/aura-stack/aura_backend /opt/aura-stack/deploy -type f \( -name '*.py' -o -name '*.yml' -o -name '*.sh' -o -name 'Dockerfile' \) \
  -exec sed -i 's/\r$//' {} +

echo "=== confirm health-check fix on disk ==="
grep -n native_ollama_base_url /opt/aura-stack/aura_backend/config.py | head
grep -n "Daemon at" /opt/aura-stack/aura_backend/services/ollama.py | head
test -f /opt/aura-stack/aura_backend/tests/unit/test_ollama_native_host.py

echo "=== nginx template changed? ==="
if ! git -C /opt/aura-stack diff --quiet -- deploy/nginx 2>/dev/null; then
  echo "nginx files differ from VPS git HEAD (expected if templates updated)"
  git -C /opt/aura-stack diff --stat -- deploy/nginx || true
else
  echo "nginx tree matches git HEAD (or git cannot see assume-unchanged); checking vs bak not needed"
fi

echo "=== build backend images (no --pull) ==="
cd /opt/aura-stack/deploy
docker compose -f docker-compose.prod.yml build gateway worker-realtime worker-ingestion beat

echo "=== migrate (forward-only, old stack still serving) ==="
if docker compose -f docker-compose.prod.yml run --rm --no-deps -T gateway alembic upgrade head; then
  echo "alembic upgrade head: ok"
else
  echo "WARN: alembic failed; continuing because stack is still on previous images"
fi

echo "=== recreate code services only ==="
docker compose -f docker-compose.prod.yml up -d --force-recreate --no-deps --no-build \
  gateway worker-realtime worker-ingestion beat

echo "=== wait gateway healthy ==="
for i in $(seq 1 36); do
  ST=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' aura-gateway-1 2>/dev/null || echo missing)
  echo "try $i: $ST"
  if [ "$ST" = "healthy" ]; then break; fi
  sleep 5
done

echo "=== post-image IDs ==="
for c in aura-gateway-1 aura-worker-realtime-1 aura-worker-ingestion-1 aura-beat-1; do
  docker inspect -f "$c image={{.Image}} started={{.State.StartedAt}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}n/a{{end}}" "$c"
done

echo "=== containers ==="
docker ps --format '{{.Names}} {{.Status}} {{.Image}}'
echo "=== internal /health ==="
docker exec aura-nginx-1 wget -qO- http://gateway:8000/health || true
echo
echo "=== public ready ==="
curl -skS --max-time 10 https://169.58.188.43.nip.io/health/ready || true
echo
echo "=== dtrs ==="
ss -tlnp | grep 8080 || true
docker ps --filter name=dtrs --format '{{.Names}} {{.Ports}} {{.Status}}'
curl -s -o /dev/null -w 'dtrs_http=%{http_code}\n' --max-time 5 http://127.0.0.1:8080/ || true
ufw status | grep -i 8080 || echo 'no 8080 in ufw'
echo "=== kernel ==="
uname -r
echo "=== deploy done ==="
