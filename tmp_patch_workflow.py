from pathlib import Path
p = Path("E:/AURA AI/.github/workflows/vps-dev-ops.yml")
t = p.read_text(encoding="utf-8")
needle = 'RUN_SEED="$RUN_SEED" \\\n                bash -s'
repl = 'RUN_SEED="$RUN_SEED" \\\n                CONSOLE_ONLY="$CONSOLE_ONLY" \\\n                bash -s'
if 'CONSOLE_ONLY="$CONSOLE_ONLY"' not in t.split("<<'REMOTE'")[0]:
    if needle not in t:
        raise SystemExit("needle missing")
    t = t.replace(needle, repl, 1)
p.write_text(t, encoding="utf-8", newline="\n")
print("ok")
