#!/bin/sh
# Assertions are `[ cond ] && ok || bad`; ok() never fails, so SC2015's caveat does not apply.
# Test fixtures have plain names, so ls-based listings are fine here.
# shellcheck disable=SC2015,SC2012,SC2129
# shellcheck source-path=SCRIPTDIR
# Round-trip tests for deploy/scripts/backup-lib.sh -- no Docker, no network, no database.
#
#   sh deploy/scripts/tests/test_backup_crypto.sh
#
# Covers: fail-closed encryption config; seal -> CHECKSUMS -> encrypted manifest; tamper
# detection (bit flip, deletion, extra file, re-checksummed ciphertext, re-encrypted substitute);
# decrypt with the right key / refusal with a wrong or missing key; plaintext opt-out; GFS
# retention selection; local prune refusing when the newest set is bad; offsite push/verify/prune
# against a stub rclone that maps a remote onto a local directory.
#
# Needs `age` and `age-keygen`. Exits 0 with SKIP if they are missing, so it can run anywhere;
# CI installs age and sets REQUIRE_AGE=1, so there a missing binary fails instead of skipping.
#
# Written in POSIX sh and meant to be run with `sh` (dash on Debian/Ubuntu), so a bashism in the
# library fails here rather than under busybox in the backup image.
set -eu

HERE=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
LIB="$HERE/../backup-lib.sh"

if ! command -v age >/dev/null 2>&1 || ! command -v age-keygen >/dev/null 2>&1; then
    if [ "${REQUIRE_AGE:-0}" = 1 ]; then
        echo "FAIL: REQUIRE_AGE=1 but age/age-keygen are not installed" >&2
        exit 1
    fi
    echo "SKIP: age/age-keygen not installed"
    exit 0
fi

T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT

log() { :; }                      # the library's own log lines are noise here
# shellcheck source=../backup-lib.sh
. "$LIB"
bk_err() { echo "    (lib) $*" >> "$T/lib-errors.log"; }

PASS=0
FAIL=0
ok()   { PASS=$((PASS + 1)); echo "ok   - $*"; }
bad()  { FAIL=$((FAIL + 1)); echo "FAIL - $*"; }
expect_ok()   { d=$1; shift; if "$@" >/dev/null 2>&1; then ok "$d"; else bad "$d"; fi; }
expect_fail() { d=$1; shift; if "$@" >/dev/null 2>&1; then bad "$d (unexpectedly succeeded)"; else ok "$d"; fi; }

age-keygen -o "$T/id.key" 2>/dev/null
age-keygen -o "$T/other.key" 2>/dev/null
RECIPIENT=$(age-keygen -y "$T/id.key")
unset BACKUP_AGE_RECIPIENTS_FILE BACKUP_AGE_IDENTITY_FILE BACKUP_ENCRYPTION 2>/dev/null || true

# ---------------------------------------------------------------------------------------------
echo "# fail-closed configuration"
unset BACKUP_AGE_RECIPIENT 2>/dev/null || true
expect_fail "age mode with no recipient refuses" bk_require_encryption_config
BACKUP_AGE_RECIPIENT=$(grep '^AGE-SECRET-KEY-' "$T/id.key"); export BACKUP_AGE_RECIPIENT
expect_fail "a private key supplied as recipient is refused" bk_require_encryption_config
BACKUP_AGE_RECIPIENT="not-a-key"
expect_fail "garbage recipient is refused" bk_require_encryption_config
BACKUP_ENCRYPTION=aes; export BACKUP_ENCRYPTION
BACKUP_AGE_RECIPIENT=$RECIPIENT
expect_fail "unknown BACKUP_ENCRYPTION is refused, not treated as plaintext" bk_require_encryption_config
unset BACKUP_ENCRYPTION
expect_ok "age mode with a valid recipient passes" bk_require_encryption_config
printf '# comment\n\n%s\n' "$RECIPIENT" > "$T/recipients.txt"
unset BACKUP_AGE_RECIPIENT
BACKUP_AGE_RECIPIENTS_FILE="$T/recipients.txt"; export BACKUP_AGE_RECIPIENTS_FILE
expect_ok "recipients file (with comments) passes" bk_require_encryption_config
BACKUP_ENCRYPTION=none; export BACKUP_ENCRYPTION
unset BACKUP_AGE_RECIPIENTS_FILE
expect_ok "explicit BACKUP_ENCRYPTION=none needs no key" bk_require_encryption_config
unset BACKUP_ENCRYPTION
BACKUP_AGE_RECIPIENT=$RECIPIENT; export BACKUP_AGE_RECIPIENT

# ---------------------------------------------------------------------------------------------
echo "# seal: encrypt -> manifest -> checksums"
STAMP=20260924T020000Z
make_staging() {
    s=$1
    mkdir -p "$s/qdrant-$STAMP"
    { echo "PLAINTEXT-MARKER-postgres"; head -c 20000 /dev/urandom; } > "$s/postgres-$STAMP.dump"
    { echo "PLAINTEXT-MARKER-redis"; head -c 3000 /dev/urandom; } > "$s/redis-$STAMP.rdb"
    { echo "PLAINTEXT-MARKER-qdrant"; head -c 5000 /dev/urandom; } > "$s/qdrant-$STAMP/knowledge v2.snapshot"
}
make_staging "$T/staging"
cp -R "$T/staging" "$T/original"
mkdir -p "$T/backups"
SET="$T/backups/aura-$STAMP"
expect_ok "bk_seal_set succeeds" bk_seal_set "$T/staging" "$SET"
if [ -f "$SET/postgres-$STAMP.dump.age" ] && [ -f "$SET/redis-$STAMP.rdb.age" ] \
    && [ -f "$SET/qdrant-$STAMP/knowledge v2.snapshot.age" ] && [ -f "$SET/MANIFEST.sha256.age" ] \
    && [ -f "$SET/CHECKSUMS.sha256" ] && [ -f "$SET/FORMAT" ]; then
    ok "set layout: every artifact .age, encrypted manifest, checksums, FORMAT"
else
    bad "set layout"; find "$SET" -type f
fi
if grep -rl "PLAINTEXT-MARKER" "$SET" >/dev/null 2>&1; then bad "plaintext found inside the sealed set"; else ok "no plaintext inside the sealed set"; fi
leftover=0
for x in "$T/backups"/.incoming-*; do [ -e "$x" ] && leftover=1; done
if [ -e "$SET/MANIFEST.sha256" ] || [ "$leftover" -eq 1 ]; then
    bad "unencrypted manifest or .incoming left behind"; else ok "no plaintext manifest or .incoming leftovers"; fi
[ "$(bk_set_encryption "$SET")" = age ] && ok "FORMAT records encryption=age" || bad "FORMAT encryption"
expect_ok "fresh set verifies without any key" bk_verify_set "$SET"

# ---------------------------------------------------------------------------------------------
echo "# tamper detection"
tamper_copy() { rm -rf "$T/tampered"; cp -R "$SET" "$T/tampered"; }

tamper_copy
f="$T/tampered/postgres-$STAMP.dump.age"
size=$(wc -c < "$f")
printf 'X' | dd of="$f" bs=1 seek=$((size / 2)) conv=notrunc 2>/dev/null
expect_fail "single-byte flip in a ciphertext fails CHECKSUMS" bk_verify_set "$T/tampered"

tamper_copy
rm "$T/tampered/redis-$STAMP.rdb.age"
expect_fail "a deleted artifact fails verification" bk_verify_set "$T/tampered"

tamper_copy
echo extra > "$T/tampered/extra.age"
expect_fail "an extra unlisted file fails verification" bk_verify_set "$T/tampered"

tamper_copy
truncate -s 100 "$T/tampered/qdrant-$STAMP/knowledge v2.snapshot.age" 2>/dev/null \
    || head -c 100 "$SET/qdrant-$STAMP/knowledge v2.snapshot.age" > "$T/tampered/qdrant-$STAMP/knowledge v2.snapshot.age"
expect_fail "a truncated artifact fails verification" bk_verify_set "$T/tampered"

BACKUP_AGE_IDENTITY_FILE="$T/id.key"; export BACKUP_AGE_IDENTITY_FILE
# An attacker who can rewrite CHECKSUMS as well: age's AEAD still rejects modified ciphertext.
tamper_copy
f="$T/tampered/redis-$STAMP.rdb.age"
size=$(wc -c < "$f")
printf 'X' | dd of="$f" bs=1 seek=$((size - 10)) conv=notrunc 2>/dev/null
bk_write_manifest "$T/tampered" "$T/tampered/CHECKSUMS.sha256" CHECKSUMS.sha256
expect_ok "(setup) re-checksummed tampered set passes the keyless check" bk_verify_set "$T/tampered"
expect_fail "...but decryption rejects the modified ciphertext" bk_open_set "$T/tampered" "$T/out-t1"
if [ -z "$(ls -A "$T/out-t1" 2>/dev/null)" ]; then ok "a failed open leaves no plaintext behind"; else bad "plaintext left after failed open"; fi

# Substitution with a validly encrypted file + regenerated CHECKSUMS: caught by the encrypted manifest.
tamper_copy
echo "attacker content" | age -e -r "$RECIPIENT" -o "$T/tampered/postgres-$STAMP.dump.age"
bk_write_manifest "$T/tampered" "$T/tampered/CHECKSUMS.sha256" CHECKSUMS.sha256
expect_fail "a re-encrypted substitute is caught by the encrypted plaintext manifest" bk_open_set "$T/tampered" "$T/out-t2"

# ---------------------------------------------------------------------------------------------
echo "# decrypt"
expect_ok "bk_open_set with the right key succeeds" bk_open_set "$SET" "$T/out"
if cmp -s "$T/original/postgres-$STAMP.dump" "$T/out/postgres-$STAMP.dump" \
    && cmp -s "$T/original/redis-$STAMP.rdb" "$T/out/redis-$STAMP.rdb" \
    && cmp -s "$T/original/qdrant-$STAMP/knowledge v2.snapshot" "$T/out/qdrant-$STAMP/knowledge v2.snapshot"; then
    ok "decrypted artifacts are byte-identical to what was dumped"
else
    bad "decrypted artifacts differ from the originals"
fi
BACKUP_AGE_IDENTITY_FILE="$T/other.key"
expect_fail "a wrong private key cannot open the set" bk_open_set "$SET" "$T/out-wrong"
unset BACKUP_AGE_IDENTITY_FILE
expect_fail "no private key configured cannot open the set" bk_open_set "$SET" "$T/out-nokey"
BACKUP_AGE_IDENTITY_FILE="$T/id.key"; export BACKUP_AGE_IDENTITY_FILE

expect_ok "backup-open.sh --verify (keyless) accepts a good set" sh "$HERE/../backup-open.sh" --verify "$SET"
tamper_copy
echo corrupt >> "$T/tampered/redis-$STAMP.rdb.age"
expect_fail "backup-open.sh --verify rejects a tampered set" sh "$HERE/../backup-open.sh" --verify "$T/tampered"
expect_ok "backup-open.sh decrypts into an empty dir" sh "$HERE/../backup-open.sh" "$SET" "$T/out-cli"

# ---------------------------------------------------------------------------------------------
echo "# two key pairs: each private key alone opens a set sealed to both public keys"
age-keygen -o "$T/second.key" 2>/dev/null
SECOND=$(age-keygen -y "$T/second.key")
printf '# key pair A\n%s\n# key pair B\n%s\n' "$RECIPIENT" "$SECOND" > "$T/two-recipients.txt"
seal_two() (
    unset BACKUP_AGE_RECIPIENT
    BACKUP_AGE_RECIPIENTS_FILE="$T/two-recipients.txt"; export BACKUP_AGE_RECIPIENTS_FILE
    bk_seal_set "$@"
)
open_with() ( BACKUP_AGE_IDENTITY_FILE=$1; export BACKUP_AGE_IDENTITY_FILE; bk_open_set "$2" "$3" )
# count_with <recipients-file or ""> [BACKUP_AGE_RECIPIENT value]
count_with() (
    unset BACKUP_AGE_RECIPIENT BACKUP_AGE_RECIPIENTS_FILE
    if [ -n "$1" ]; then BACKUP_AGE_RECIPIENTS_FILE=$1; export BACKUP_AGE_RECIPIENTS_FILE; fi
    if [ -n "${2:-}" ]; then BACKUP_AGE_RECIPIENT=$2; export BACKUP_AGE_RECIPIENT; fi
    bk_recipient_count
)

make_staging "$T/staging-two"
cp -R "$T/staging-two" "$T/original-two"
mkdir -p "$T/backups-two"
SET_TWO="$T/backups-two/aura-$STAMP"
expect_ok "a set seals to both public keys" seal_two "$T/staging-two" "$SET_TWO"
expect_ok "private key A alone opens it" open_with "$T/id.key" "$SET_TWO" "$T/out-two-a"
expect_ok "private key B alone opens it" open_with "$T/second.key" "$SET_TWO" "$T/out-two-b"
if cmp -s "$T/original-two/postgres-$STAMP.dump" "$T/out-two-a/postgres-$STAMP.dump" \
    && cmp -s "$T/original-two/postgres-$STAMP.dump" "$T/out-two-b/postgres-$STAMP.dump" \
    && cmp -s "$T/original-two/redis-$STAMP.rdb" "$T/out-two-b/redis-$STAMP.rdb"; then
    ok "...and each recovers byte-identical data"
else
    bad "a key recovered different data"
fi
expect_fail "a third key that was not listed cannot open it" open_with "$T/other.key" "$SET_TWO" "$T/out-two-x"

[ "$(count_with "$T/two-recipients.txt")" = 2 ] && ok "two distinct public keys count as 2" \
    || bad "two keys counted as $(count_with "$T/two-recipients.txt")"
printf '%s\n%s\n' "$RECIPIENT" "$RECIPIENT" > "$T/dup-recipients.txt"
[ "$(count_with "$T/dup-recipients.txt")" = 1 ] && ok "the same key listed twice counts as 1" \
    || bad "a duplicated key counted as $(count_with "$T/dup-recipients.txt")"
[ "$(count_with "" "$RECIPIENT,$RECIPIENT")" = 1 ] && ok "...also when repeated in BACKUP_AGE_RECIPIENT" \
    || bad "repeated BACKUP_AGE_RECIPIENT counted as $(count_with "" "$RECIPIENT,$RECIPIENT")"
[ "$(count_with "$T/dup-recipients.txt" "$SECOND")" = 2 ] && ok "the file and BACKUP_AGE_RECIPIENT combine" \
    || bad "file + env counted as $(count_with "$T/dup-recipients.txt" "$SECOND")"
[ "$(count_with "" "not-a-key")" = 0 ] && ok "an invalid recipient list counts as 0" \
    || bad "invalid list counted as $(count_with "" "not-a-key")"

# ---------------------------------------------------------------------------------------------
echo "# explicit plaintext opt-out"
BACKUP_ENCRYPTION=none; export BACKUP_ENCRYPTION
make_staging "$T/staging-none"
SET_NONE="$T/backups-none/aura-$STAMP"
mkdir -p "$T/backups-none"
expect_ok "mode none seals" bk_seal_set "$T/staging-none" "$SET_NONE"
[ "$(bk_set_encryption "$SET_NONE")" = none ] && ok "FORMAT records encryption=none" || bad "FORMAT none"
expect_ok "mode none set verifies" bk_verify_set "$SET_NONE"
expect_ok "mode none set opens" bk_open_set "$SET_NONE" "$T/out-none"
unset BACKUP_ENCRYPTION

# ---------------------------------------------------------------------------------------------
echo "# GFS retention selection"
names="$T/names.txt"
: > "$names"
d=0
while [ "$d" -lt 90 ]; do
    date -u -d "2026-01-01 +$d days" +aura-%Y%m%dT020000Z >> "$names"
    d=$((d + 1))
done
echo "aura-20260331T140000Z" >> "$names"      # a second, newer set on the last day
echo "not-a-set" >> "$names"
echo "aura-2026" >> "$names"
bk_retention_prune_list 7 4 3 < "$names" > "$T/pruned.txt"
kept=$(grep -vxF -f "$T/pruned.txt" "$names" | grep '^aura-........T......Z$' | LC_ALL=C sort -r | tr '\n' ' ')
want="aura-20260331T140000Z aura-20260330T020000Z aura-20260329T020000Z aura-20260328T020000Z aura-20260327T020000Z aura-20260326T020000Z aura-20260325T020000Z aura-20260322T020000Z aura-20260315T020000Z aura-20260228T020000Z aura-20260131T020000Z "
if [ "$kept" = "$want" ]; then ok "7 daily + 4 weekly + 3 monthly keeps exactly the expected 11 sets"; else bad "retention kept: $kept"; fi
if grep -q -e '^not-a-set$' -e '^aura-2026$' "$T/pruned.txt"; then bad "malformed names were selected for deletion"; else ok "malformed names are never selected for deletion"; fi
if grep -q 'aura-20260331T020000Z' "$T/pruned.txt"; then ok "older same-day set is pruned (daily keeps newest per day)"; else bad "same-day older set not pruned"; fi
one=$(printf 'aura-20260101T000000Z\n' | bk_retention_prune_list 0 0 0)
[ -z "$one" ] && ok "the newest set is kept even with all counts at 0" || bad "newest set pruned at 0/0/0"

echo "# local prune"
LP="$T/localprune"
mkdir -p "$LP"
for s in 20260101T020000Z 20260102T020000Z 20260103T020000Z; do
    mkdir -p "$T/st-$s"; echo "data $s" > "$T/st-$s/postgres-$s.dump"
    bk_seal_set "$T/st-$s" "$LP/aura-$s" >/dev/null
done
# Corrupt the NEWEST set: nothing may be deleted.
echo corrupt >> "$LP/aura-20260103T020000Z/postgres-20260103T020000Z.dump.age"
expect_fail "prune refuses when the newest set fails verification" bk_prune_local "$LP" 1 0 0
[ "$(ls "$LP" | wc -l | tr -d ' ')" = 3 ] && ok "...and deleted nothing" || bad "sets deleted despite a bad newest set"
rm -rf "$LP/aura-20260103T020000Z"
expect_ok "prune runs when the newest set verifies" bk_prune_local "$LP" 1 0 0
[ "$(ls "$LP")" = "aura-20260102T020000Z" ] && ok "...keeping only the newest (keep_daily=1)" || bad "local prune result: $(ls "$LP" | tr '\n' ' ')"

# ---------------------------------------------------------------------------------------------
echo "# offsite push / verify / prune (stub rclone)"
mkdir -p "$T/bin" "$T/remote"
cat > "$T/bin/rclone" <<'STUB'
#!/bin/sh
# Minimal rclone stand-in: "stub<anything>:<path>" maps to $STUB_ROOT/<path>, so tests can name
# several distinct remotes (stub-a:, stub-b:) that share one local root.
set -eu
map() { case "$1" in stub*:*) echo "$STUB_ROOT/${1#*:}" ;; *) echo "$1" ;; esac; }
cmd=$1; shift
case "$cmd" in
    copy)
        src=$(map "$1"); dst=$(map "$2"); mkdir -p "$dst"; cp -R "$src/." "$dst/"
        corrupt=${STUB_CORRUPT_COPY:-0}
        if [ -n "${STUB_CORRUPT_DST:-}" ]; then
            case "$2" in *"$STUB_CORRUPT_DST"*) corrupt=1 ;; esac
        fi
        if [ "$corrupt" = 1 ]; then
            f=$(find "$dst" -name '*.dump.age' | head -n 1); printf 'Z' | dd of="$f" bs=1 seek=40 conv=notrunc 2>/dev/null
        fi ;;
    check)
        src=$(map "$1"); dst=$(map "$2")
        (cd "$src" && find . -type f) | while IFS= read -r f; do cmp -s "$src/$f" "$dst/$f" || { echo "differ: $f" >&2; exit 1; }; done ;;
    cat) cat "$(map "$1")" ;;
    lsf) shift_dirs=$1; [ "$shift_dirs" = --dirs-only ] && shift; d=$(map "$1"); (cd "$d" && for x in *; do [ -d "$x" ] && echo "$x/"; done) || true ;;
    purge) rm -rf "$(map "$1")" ;;
    delete) : ;;
    *) echo "stub rclone: unsupported $cmd" >&2; exit 2 ;;
esac
STUB
chmod +x "$T/bin/rclone"
STUB_ROOT="$T/remote"; export STUB_ROOT
PATH="$T/bin:$PATH"

expect_ok "push of an encrypted set uploads and verifies" bk_offsite_push "$SET" "stub:bucket"
expect_ok "remote copy verifies keyless after upload" bk_verify_set "$T/remote/bucket/aura-$STAMP"
STUB_CORRUPT_COPY=1; export STUB_CORRUPT_COPY
rm -rf "$T/remote/bucket"
expect_fail "a corrupted upload is caught by remote verification" bk_offsite_push "$SET" "stub:bucket"
unset STUB_CORRUPT_COPY
expect_fail "an unencrypted set is never shipped offsite" bk_offsite_push "$SET_NONE" "stub:bucket-none"
[ ! -e "$T/remote/bucket-none" ] && ok "...and nothing reached the remote" || bad "plaintext reached the remote"

rm -rf "$T/remote/bucket"
for s in 20260801T020000Z 20260802T020000Z 20260803T020000Z; do mkdir -p "$T/remote/bucket/aura-$s"; echo x > "$T/remote/bucket/aura-$s/f"; done
expect_ok "(setup) push current set" bk_offsite_push "$SET" "stub:bucket"
expect_ok "offsite prune after a verified push" bk_offsite_prune "stub:bucket" "aura-$STAMP" 2 0 0
[ "$(ls "$T/remote/bucket" | tr '\n' ' ')" = "aura-20260803T020000Z aura-$STAMP " ] \
    && ok "...keeps the newest two dailies" || bad "offsite prune result: $(ls "$T/remote/bucket" | tr '\n' ' ')"
mkdir -p "$T/remote/bucket/aura-20991231T000000Z"
expect_fail "offsite prune refuses when the newest remote set is not the one just verified" \
    bk_offsite_prune "stub:bucket" "aura-$STAMP" 1 0 0
[ -d "$T/remote/bucket/aura-20260803T020000Z" ] && ok "...and deletes nothing" || bad "offsite deleted despite refusal"

# ---------------------------------------------------------------------------------------------
echo "# multi-target offsite quorum (copy_offsite and resolve_offsite_remote, from backup.sh)"
# Extracted rather than sourced: backup.sh runs its cycle loop as soon as it is loaded.
sed -n '/^offsite_config_error() (/,/^)/p; /^offsite_required_metric() {/,/^}/p; /^resolve_offsite_remote() {/,/^}/p; /^copy_offsite() {/,/^}/p' \
    "$HERE/../backup.sh" > "$T/offsite-funcs.sh"
for fn in offsite_config_error offsite_required_metric resolve_offsite_remote copy_offsite; do
    grep -q "^$fn() " "$T/offsite-funcs.sh" && ok "extracted $fn from backup.sh" || bad "could not extract $fn"
done
RCLONE_CONFIG_FILE="$T/rclone.conf"; export RCLONE_CONFIG_FILE
: > "$RCLONE_CONFIG_FILE"

# offsite_case <min> <target>...  -- runs copy_offsite in a subshell; writes the verified count to $T/verified
# The OFFSITE_* variables are read by the function sourced from $T below, which shellcheck cannot see.
# shellcheck disable=SC2034,SC1091
offsite_case() (
    OFFSITE_MIN_COPIES=$1; shift
    OFFSITE_TARGETS="$*"
    OFFSITE_TARGET_COUNT=$#
    OFFSITE_KEEP_DAILY=7; OFFSITE_KEEP_WEEKLY=4; OFFSITE_KEEP_MONTHLY=6
    BACKUP_DIR="$T/offsite-dir"; STAGING="$T/offsite-staging"
    mkdir -p "$BACKUP_DIR" "$STAGING"
    . "$T/offsite-funcs.sh"
    rc=0
    copy_offsite "$SET" || rc=$?
    echo "$OFFSITE_VERIFIED" > "$T/verified"
    exit "$rc"
)
verified() { cat "$T/verified"; }

rm -rf "$T/offsite-dir" "$T/remote/ob-good-a" "$T/remote/ob-good-b" "$T/remote/ob-bad"
expect_ok "two targets both verify: durable at min=2" offsite_case 2 "stub-a:ob-good-a" "stub-b:ob-good-b"
[ "$(verified)" = 2 ] && ok "...verified count is 2" || bad "verified count $(verified), wanted 2"
[ -f "$T/offsite-dir/.last_offsite_success" ] && ok "...and the success marker advances" || bad "no success marker after a durable cycle"

rm -rf "$T/offsite-dir" "$T/remote/ob-good-a" "$T/remote/ob-good-b" "$T/remote/ob-bad"
STUB_CORRUPT_DST=ob-bad; export STUB_CORRUPT_DST
expect_fail "one of two targets corrupt at min=2: the cycle reports failure" offsite_case 2 "stub-a:ob-bad" "stub-b:ob-good-a"
[ "$(verified)" = 1 ] && ok "...only the good target counts as verified" || bad "verified count $(verified), wanted 1"
[ -d "$T/remote/ob-good-a/aura-$STAMP" ] && ok "...the good copy is still uploaded" || bad "good copy missing"
[ ! -f "$T/offsite-dir/.last_offsite_success" ] && ok "...and the success marker does NOT advance" || bad "success marker advanced on an under-replicated set"
unset STUB_CORRUPT_DST

rm -rf "$T/offsite-dir" "$T/remote/ob-good-a" "$T/remote/ob-bad"
STUB_CORRUPT_DST=ob-bad; export STUB_CORRUPT_DST
expect_ok "the same split passes when min=1" offsite_case 1 "stub-a:ob-bad" "stub-b:ob-good-a"
[ "$(verified)" = 1 ] && ok "...with one verified copy" || bad "verified count $(verified)"
unset STUB_CORRUPT_DST

rm -rf "$T/offsite-dir" "$T/remote/ob-good-a"
expect_fail "one target cannot meet min=2 even when it verifies" offsite_case 2 "stub-a:ob-good-a"
[ "$(verified)" = 1 ] && ok "...it is still uploaded and counted" || bad "single target not counted"

rm -rf "$T/offsite-dir" "$T/remote/ob-good-a" "$T/remote/ob-good-b"
RCLONE_CONFIG_FILE="$T/no-such.conf"; export RCLONE_CONFIG_FILE
expect_fail "several targets without named remotes in rclone.conf are refused" offsite_case 2 "stub-a:ob-good-a" "stub-b:ob-good-b"
[ ! -e "$T/remote/ob-good-a" ] && ok "...and nothing is uploaded" || bad "upload happened without a named-remote config"
RCLONE_CONFIG_FILE="$T/rclone.conf"; export RCLONE_CONFIG_FILE

echo "# offsite configuration is checked before any upload"
rm -rf "$T/remote/ob-good-a" "$T/remote/ob-good-b"
expect_fail "two buckets behind one rclone remote do not count as two copies" offsite_case 2 "stub-a:ob-good-a" "stub-a:ob-good-b"
[ ! -e "$T/remote/ob-good-a" ] && ok "...and nothing is uploaded" || bad "uploaded despite a shared remote"
expect_fail "the same target listed twice is refused" offsite_case 2 "stub-a:ob-good-a" "stub-a:ob-good-a"
expect_fail "a non-numeric BACKUP_OFFSITE_MIN_COPIES is refused" offsite_case two "stub-a:ob-good-a" "stub-b:ob-good-b"
expect_fail "BACKUP_OFFSITE_MIN_COPIES=0 is refused" offsite_case 0 "stub-a:ob-good-a" "stub-b:ob-good-b"
expect_fail "a target with a glob character is refused" offsite_case 1 "stub-a:ob-*"
[ ! -e "$T/remote/ob-good-a" ] && [ ! -e "$T/remote/ob-good-b" ] && ok "...none of them uploaded anything" || bad "an invalid configuration uploaded"
# shellcheck disable=SC2034,SC1091 # read by the sourced offsite_required_metric
metric_for() ( OFFSITE_MIN_COPIES=$1; . "$T/offsite-funcs.sh"; offsite_required_metric )
[ "$(metric_for two)" = 1 ] && [ "$(metric_for 0)" = 1 ] && [ "$(metric_for 3)" = 3 ] \
    && ok "the required-copies gauge stays a positive number for any setting" \
    || bad "required-copies gauge: two=$(metric_for two) 0=$(metric_for 0) 3=$(metric_for 3)"

echo "# the shipped B2 + R2 rclone example is a configuration backup.sh accepts"
EXAMPLE="$HERE/../../backup/rclone.conf.example"
remotes=$(sed -n 's/^\[\(.*\)\]$/\1/p' "$EXAMPLE")
[ "$(echo "$remotes" | sort -u | wc -l | tr -d ' ')" = 2 ] \
    && ok "the example names two distinct remotes ($(echo "$remotes" | tr '\n' ' '))" || bad "example remotes: $remotes"
example_targets=$(echo "$remotes" | sed 's/$/:aura-backups/' | tr '\n' ' ')
# shellcheck disable=SC2034,SC1091 # read by the sourced offsite_config_error
example_problem=$( OFFSITE_MIN_COPIES=2; OFFSITE_TARGETS=$example_targets; . "$T/offsite-funcs.sh"; offsite_config_error )
[ -z "$example_problem" ] && ok "...and backup.sh accepts them as two independent targets" || bad "example rejected: $example_problem"
grep -Eq '^[[:space:]]*disable_checksum' "$EXAMPLE" \
    && bad "the example disables upload checksums (rclone check would compare sizes only)" \
    || ok "the example keeps upload checksums on"

echo "# backup-fetch.sh: newest set from the first target whose copy verifies"
FETCH="$HERE/../backup-fetch.sh"
OLD=aura-20260101T000000Z
reset_fetch() { rm -rf "$T/remote/fetch-a" "$T/remote/fetch-b" "$T/fetch-out"; mkdir -p "$T/remote/fetch-a" "$T/remote/fetch-b"; }
corrupt_remote() { f=$(find "$1" -name '*.dump.age' | head -n 1); printf 'Z' | dd of="$f" bs=1 seek=40 conv=notrunc 2>/dev/null; }
fetch() { BACKUP_OFFSITE_TARGETS="stub-a:fetch-a stub-b:fetch-b" sh "$FETCH" "$T/fetch-out" "$@" 2>>"$T/fetch.log"; }

reset_fetch
cp -R "$SET" "$T/remote/fetch-a/$OLD"
cp -R "$SET" "$T/remote/fetch-b/aura-$STAMP"
got=$(fetch) && [ "$got" = "$T/fetch-out/aura-$STAMP" ] \
    && ok "picks the newest set held by any target, not the first target's newest" || bad "fetched '$got'"
expect_ok "...and the fetched copy verifies" bk_verify_set "$T/fetch-out/aura-$STAMP"

reset_fetch
cp -R "$SET" "$T/remote/fetch-a/aura-$STAMP"; corrupt_remote "$T/remote/fetch-a/aura-$STAMP"
cp -R "$SET" "$T/remote/fetch-b/aura-$STAMP"
got=$(fetch) && [ "$got" = "$T/fetch-out/aura-$STAMP" ] && bk_verify_set "$got" >/dev/null 2>&1 \
    && ok "a corrupt copy at the first target falls through to a good one" || bad "corrupt fall-through: '$got'"

reset_fetch
cp -R "$SET" "$T/remote/fetch-a/aura-$STAMP"; corrupt_remote "$T/remote/fetch-a/aura-$STAMP"
cp -R "$SET" "$T/remote/fetch-b/aura-$STAMP"; corrupt_remote "$T/remote/fetch-b/aura-$STAMP"
expect_fail "no verifying copy anywhere fails" fetch
[ ! -e "$T/fetch-out/aura-$STAMP" ] && ok "...and leaves no partial set behind" || bad "a corrupt copy was left in place"

reset_fetch
cp -R "$SET" "$T/remote/fetch-a/$OLD"
cp -R "$SET" "$T/remote/fetch-b/aura-$STAMP"
got=$(fetch "$OLD") && [ "$got" = "$T/fetch-out/$OLD" ] && ok "an older set can be fetched by name" || bad "named fetch: '$got'"
expect_fail "an existing destination set is not overwritten" fetch "$OLD"
expect_fail "a name that is not a set name is refused" fetch "../etc"

# ---------------------------------------------------------------------------------------------
echo "# Railway inputs: Qdrant Cloud, connection URLs (backup_* functions from backup.sh, stubbed services)"
SVC="$T/bin-svc"
mkdir -p "$SVC"
cat > "$SVC/wget" <<'STUB'
#!/bin/sh
# Qdrant stand-in: two collections, a snapshot per collection, and DELETE. Logs every call.
echo "$*" >> "$WGET_LOG"
out=""; url=""; method=GET
while [ $# -gt 0 ]; do
    case "$1" in
        -qO-) out=- ;;
        -qO|-O) shift; out=$1 ;;
        --post-data=*) method=POST ;;
        --method=*) method=${1#--method=} ;;
        http*) url=$1 ;;
    esac
    shift
done
case "$method $url" in
    "GET "*/collections) body='{"result":{"collections":[{"name":"knowledge"},{"name":"memory"}]},"status":"ok"}' ;;
    "POST "*/snapshots) c=${url%/snapshots}; c=${c##*/}; body="{\"result\":{\"name\":\"$c-1.snapshot\",\"size\":9},\"status\":\"ok\"}" ;;
    "GET "*/snapshots/*) body="SNAPSHOT-BYTES" ;;
    "DELETE "*) body='{"result":true}' ;;
    *) exit 8 ;;
esac
if [ "$out" = - ]; then printf '%s' "$body"; else printf '%s' "$body" > "$out"; fi
STUB
cat > "$SVC/pg_dump" <<'STUB'
#!/bin/sh
echo "$*" > "$PGDUMP_LOG"
for a in "$@"; do case "$a" in --file=*) echo "PGDMP" > "${a#--file=}" ;; esac; done
STUB
cat > "$SVC/pg_restore" <<'STUB'
#!/bin/sh
echo "3445; 0 16384 TABLE DATA public users postgres"
STUB
cat > "$SVC/redis-cli" <<'STUB'
#!/bin/sh
echo "$*" > "$REDIS_LOG"
while [ $# -gt 0 ]; do [ "$1" = --rdb ] && { shift; echo "REDIS0011" > "$1"; }; shift; done
STUB
chmod +x "$SVC/wget" "$SVC/pg_dump" "$SVC/pg_restore" "$SVC/redis-cli"
sed -n '/^backup_postgres() {/,/^}/p; /^backup_qdrant() {/,/^}/p; /^backup_redis() {/,/^}/p' \
    "$HERE/../backup.sh" > "$T/svc-funcs.sh"
WGET_LOG="$T/wget.log"; PGDUMP_LOG="$T/pgdump.log"; REDIS_LOG="$T/redis.log"
export WGET_LOG PGDUMP_LOG REDIS_LOG
# Variables are read by the sourced backup_* functions; PATH is changed only inside the subshell.
# shellcheck disable=SC2034,SC1091,SC2030
svc_run() (
    PATH="$SVC:$PATH"; STAGING="$T/svc-staging"; QDRANT_URL=https://qdrant.example
    mkdir -p "$STAGING"
    . "$T/svc-funcs.sh"
    "$@"
)

: > "$WGET_LOG"
rm -rf "$T/svc-staging"
QDRANT_API_KEY=qkey-from-env; export QDRANT_API_KEY
expect_ok "backup_qdrant runs against a key-protected Qdrant" svc_run backup_qdrant S1
[ -f "$T/svc-staging/qdrant-S1/knowledge.snapshot" ] && [ -f "$T/svc-staging/qdrant-S1/memory.snapshot" ] \
    && ok "EVERY collection is snapshotted, not only the last one in the JSON" \
    || bad "collections snapshotted: $(ls "$T/svc-staging/qdrant-S1" 2>/dev/null | tr '\n' ' ')"
[ "$(grep -c -- '--method=DELETE' "$WGET_LOG")" = 2 ] && ok "...and each snapshot is deleted from Qdrant afterwards" \
    || bad "DELETE calls: $(grep -c -- '--method=DELETE' "$WGET_LOG")"
grep -q 'api-key: qkey-from-env' "$WGET_LOG" && ok "the API key comes from QDRANT_API_KEY when there is no secret file" \
    || bad "QDRANT_API_KEY not used"
unset QDRANT_API_KEY

BACKUP_DATABASE_URL='postgresql+asyncpg://u:p@db.internal:5432/railway'; export BACKUP_DATABASE_URL
expect_ok "backup_postgres dumps from BACKUP_DATABASE_URL" svc_run backup_postgres S2
grep -q -- '--dbname=postgresql://u:p@db.internal:5432/railway' "$PGDUMP_LOG" \
    && ok "...with the SQLAlchemy driver suffix stripped for libpq" || bad "pg_dump args: $(cat "$PGDUMP_LOG")"
unset BACKUP_DATABASE_URL
expect_ok "backup_postgres without a URL still uses the PG* variables" svc_run backup_postgres S3
grep -q -- '--dbname' "$PGDUMP_LOG" && bad "pg_dump got --dbname without BACKUP_DATABASE_URL" \
    || ok "...and passes no --dbname"

BACKUP_REDIS_URL='redis://default:rpw@redis.internal:6379'; export BACKUP_REDIS_URL
expect_ok "backup_redis dumps from BACKUP_REDIS_URL" svc_run backup_redis S4
grep -q -- '-u redis://default:rpw@redis.internal:6379' "$REDIS_LOG" && ok "...through redis-cli -u" \
    || bad "redis-cli args: $(cat "$REDIS_LOG")"
unset BACKUP_REDIS_URL

echo "# BACKUP_RUN_ONCE: one cycle, exit status for the scheduler"
mkdir -p "$T/once-dir" "$T/once-metrics"
once_status=0
# shellcheck disable=SC2031 # the stubbed PATH is meant for this one run only
(
    unset BACKUP_AGE_RECIPIENT BACKUP_AGE_RECIPIENTS_FILE
    BACKUP_RUN_ONCE=1 BACKUP_ENCRYPTION=age BACKUP_DIR="$T/once-dir" BACKUP_METRICS_DIR="$T/once-metrics" \
        PATH="$SVC:$PATH" timeout 60 sh "$HERE/../backup.sh" > "$T/once.log" 2>&1
) || once_status=$?
[ "$once_status" = 1 ] && ok "a refused cycle exits 1 instead of sleeping until the next one" \
    || bad "BACKUP_RUN_ONCE exit status $once_status (124 = it looped)"
grep -q 'aura_backup_config_error 1' "$T/once-metrics/aura_backup.prom" && ok "...after writing its metrics" \
    || bad "no metrics written in once mode"

echo "# the Railway image stays in step with the VPS image"
VPS_DF="$HERE/../../backup/Dockerfile"
RAIL_DF="$HERE/../../backup/railway.Dockerfile"
[ "$(grep 'apk add' "$VPS_DF")" = "$(grep 'apk add' "$RAIL_DF")" ] \
    && ok "both images install the same packages" || bad "apk lines differ between the two backup Dockerfiles"
vps_alpine=$(sed -n 's/^FROM postgres:[0-9]*-\(alpine[0-9.]*\).*/\1/p' "$VPS_DF")
grep -q "^FROM postgres:\${PG_MAJOR}-$vps_alpine\$" "$RAIL_DF" && ok "...on the same Alpine branch ($vps_alpine)" \
    || bad "the Railway image is not on $vps_alpine"
missing=""
for s in backup.sh backup-lib.sh backup-fetch.sh backup-open.sh restore.sh restore-drill.sh; do
    grep -q "scripts/$s" "$RAIL_DF" || missing="$missing $s"
done
[ -z "$missing" ] && ok "the Railway image copies every backup script" || bad "not copied into the Railway image:$missing"
rail_major=$(sed -n 's/^ARG PG_MAJOR=\([0-9]*\)$/\1/p' "$RAIL_DF")
# shellcheck disable=SC2016 # the ${...} is matched literally in restore-drill.sh's text
drill_major=$(sed -n 's/^DRILL_PG_IMAGE=\${DRILL_PG_IMAGE:-postgres:\([0-9]*\)-alpine}$/\1/p' "$HERE/../restore-drill.sh")
if [ -n "$rail_major" ] && [ -n "$drill_major" ] && [ "$drill_major" -ge "$rail_major" ]; then
    ok "the restore drill's Postgres ($drill_major) can read what the Railway image's pg_dump ($rail_major) writes"
else
    bad "restore-drill.sh Postgres '$drill_major' is older than the Railway image's PG_MAJOR '$rail_major'"
fi

# ---------------------------------------------------------------------------------------------
echo "# library functions do not clobber caller variables"
set_dir=caller-set; remote=caller-remote; names=caller-names; dir=caller-dir; mode=caller-mode
bk_verify_set "$SET" >/dev/null 2>&1 || true
bk_offsite_push "$SET" "stub:bucket" >/dev/null 2>&1 || true
bk_prune_local "$LP" 1 0 0 >/dev/null 2>&1 || true
bk_seal_set "$T/staging" "$T/backups/aura-20260925T000000Z" >/dev/null 2>&1 || true
if [ "$set_dir/$remote/$names/$dir/$mode" = "caller-set/caller-remote/caller-names/caller-dir/caller-mode" ]; then
    ok "set_dir/remote/names/dir/mode survive library calls"
else
    bad "library clobbered caller variables: $set_dir/$remote/$names/$dir/$mode"
fi

# ---------------------------------------------------------------------------------------------
echo "# scripts parse"
for s in backup.sh backup-lib.sh backup-open.sh backup-fetch.sh restore.sh restore-drill.sh; do
    expect_ok "sh -n $s" sh -n "$HERE/../$s"
done

echo
echo "passed: $PASS  failed: $FAIL"
if [ "$FAIL" -ne 0 ]; then
    echo "library errors logged during the run:"; cat "$T/lib-errors.log" 2>/dev/null || true
    exit 1
fi
