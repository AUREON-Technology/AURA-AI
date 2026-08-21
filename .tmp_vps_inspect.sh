#!/bin/bash
set -euo pipefail
echo '=== ENV KEYS (names only) ==='
grep -E '^[A-Z_]+=' /opt/aura-stack/deploy/.env | cut -d= -f1
echo '=== ENV SAFE LLM/KNOWLEDGE/MEMORY PINS ==='
grep -E '^(AURA_LLM_|AURA_PROVIDER_LOCAL_|AURA_LOCAL_|AURA_KNOWLEDGE_|AURA_MEMORY_|OLLAMA_)' /opt/aura-stack/deploy/.env || true
echo '=== COMPOSE PINS ==='
grep -nE 'TOP_K|HISTORY|CHUNK|PROMPT|TIMEOUT|3b|OLLAMA_NUM_PARALLEL|cpus: "6' /opt/aura-stack/deploy/docker-compose.prod.yml || true
echo '=== HOST FILE MARKERS ==='
for f in \
  /opt/aura-stack/aura_backend/aura_core/config/settings.py \
  /opt/aura-stack/aura_backend/aura_core/context/engine.py \
  /opt/aura-stack/aura_backend/aura_core/context/__init__.py \
  /opt/aura-stack/aura_backend/aura_core/prompts/library.py \
  /opt/aura-stack/aura_backend/aura_core/kernel/kernel.py \
  /opt/aura-stack/aura_backend/aura_core/knowledge/engine.py \
  /opt/aura-stack/aura_backend/aura_core/container.py \
  /opt/aura-stack/aura_backend/aura_core/domain/context.py
do
  echo "-- $f"
  if [ -f "$f" ]; then
    grep -n 'max_prompt_tokens\|DEFAULT_MAX_PROMPT\|prompt_chunk_max_chars\|retrieval_top_k\|version="1.1.0"' "$f" | head -20 || true
  else
    echo 'MISSING'
  fi
done
echo '=== RUNNING IMAGE IMPORT ==='
docker exec aura-gateway-1 python - <<'PY'
from aura_core.config.settings import LLMSettings, KnowledgeSettings
s = LLMSettings.from_env()
k = KnowledgeSettings.from_env()
print('max_prompt_tokens', s.max_prompt_tokens)
print('timeout_s', s.request_timeout_s)
print('top_k', k.retrieval_top_k)
print('prompt_chunk', k.prompt_chunk_max_chars)
import aura_core.context.engine as e
print('DEFAULT_MAX_PROMPT_TOKENS', getattr(e, 'DEFAULT_MAX_PROMPT_TOKENS', 'MISSING'))
from aura_core.prompts.library import PromptLibrary
print('prompt_version_sample', PromptLibrary().get('system.chat').version if hasattr(PromptLibrary(), 'get') else 'n/a')
PY
echo '=== GATEWAY ENV SAFE ==='
docker exec aura-gateway-1 sh -c 'printf "TIMEOUT=%s MODEL=%s TOP_K=%s PROMPT_TOKENS=%s\n" "$AURA_LLM_TIMEOUT_S" "$AURA_PROVIDER_LOCAL_MODEL" "$AURA_KNOWLEDGE_TOP_K" "$AURA_LLM_MAX_PROMPT_TOKENS"'
echo '=== IMAGE GREP ==='
docker exec aura-gateway-1 grep -n 'max_prompt_tokens\|DEFAULT_MAX_PROMPT_TOKENS = 400\|version="1.1.0"' /app/aura_core/config/settings.py /app/aura_core/context/engine.py /app/aura_core/prompts/library.py | head -40
