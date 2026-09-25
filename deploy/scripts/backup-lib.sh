#!/bin/sh
# Shared backup-set primitives: encryption, integrity manifests, retention, offsite verification.
#
# Sourced (never executed) by backup.sh, restore.sh, restore-drill.sh, backup-open.sh and
# tests/test_backup_crypto.sh. POSIX sh: it runs under busybox ash in the `backup` image
# (postgres:16-alpine) and under dash/bash on the host, so no arrays, no [[ ]], no pipefail.
#
# A BACKUP SET is one directory per cycle, named aura-<YYYYmmddTHHMMSSZ>:
#
#   aura-20260924T020000Z/
#     FORMAT                           "aura-backup-set 1" + "encryption=age|none"
#     postgres-<stamp>.dump.age        every artifact encrypted individually (age, X25519)
#     redis-<stamp>.rdb.age
#     qdrant-<stamp>/<collection>.snapshot.age
#     MANIFEST.sha256.age              SHA-256 of every *plaintext* artifact, itself encrypted
#     CHECKSUMS.sha256                 SHA-256 of every stored file (ciphertext + FORMAT + manifest)
#
# Why two manifests. CHECKSUMS.sha256 is checkable WITHOUT the private key -- on the backup host,
# after an offsite download, before anyone fetches the key from offline storage -- and it lists
# the set's complete contents, so a missing, truncated, swapped-in or extra file fails
# verification. MANIFEST.sha256.age is checked after decryption and proves the plaintext that
# comes out is byte-for-byte what was dumped and verified. age's own AEAD already rejects any
# modified ciphertext; the manifests add completeness and an end-to-end plaintext check.
#
# Threat model, stated plainly: age is public-key encryption, so the backup host holds only the
# RECIPIENT (public key). A compromised host cannot decrypt any set, past or future. It can still
# delete sets, or write new forged ones (anyone holding a public key can encrypt to it) -- so the
# manifests detect corruption and tampering by anyone who cannot also regenerate them, not a
# fully compromised host. Offsite object-lock/versioning is what defends against deletion.
#
# Every bk_* function body is a SUBSHELL -- `name() ( ... )`, not `{ ... }` -- and exits rather
# than returns. POSIX sh has no local variables, so a brace-bodied helper assigning `set_dir` or
# `remote` silently overwrote the caller's variable of the same name (backup.sh's offsite prune
# once listed the wrong path because of exactly that). A subshell body cannot leak.
#
# Callers may define log(); a default is provided.

BK_SET_PREFIX=aura-
BK_FORMAT_FILE=FORMAT
BK_MANIFEST=MANIFEST.sha256
BK_CHECKSUMS=CHECKSUMS.sha256

if ! command -v log >/dev/null 2>&1; then
    log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [backup] $*"; }
fi

bk_err() { log "ERROR: $*" >&2; }

# ---------------------------------------------------------------------------------------------
# Encryption configuration
# ---------------------------------------------------------------------------------------------

# BACKUP_ENCRYPTION: age (default) | none. Anything else is a configuration error, not a
# fallback to plaintext.
bk_encryption_mode() (
    mode=${BACKUP_ENCRYPTION:-age}
    case "$mode" in
        age|none) echo "$mode" ;;
        *) bk_err "BACKUP_ENCRYPTION='$mode' is not supported (use 'age', or explicitly 'none')"; exit 1 ;;
    esac
)

# Writes the effective recipient list (BACKUP_AGE_RECIPIENTS_FILE lines + BACKUP_AGE_RECIPIENT
# values) to $1, one per line, comments/blanks dropped. Returns 1 when there is none or a line
# is not an age/SSH public key -- a private key pasted into the wrong variable is refused here
# rather than silently used as a recipient.
bk_write_recipients() (
    out=$1
    : > "$out"
    if [ -n "${BACKUP_AGE_RECIPIENTS_FILE:-}" ] && [ -f "$BACKUP_AGE_RECIPIENTS_FILE" ]; then
        grep -v '^[[:space:]]*#' "$BACKUP_AGE_RECIPIENTS_FILE" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' \
            | grep -v '^$' >> "$out" || true
    fi
    if [ -n "${BACKUP_AGE_RECIPIENT:-}" ]; then
        # Space- or comma-separated list of age1... keys.
        for r in $(echo "$BACKUP_AGE_RECIPIENT" | tr ',' ' '); do
            echo "$r" >> "$out"
        done
    fi
    if [ ! -s "$out" ]; then
        exit 1
    fi
    while IFS= read -r line; do
        case "$line" in
            age1*|ssh-ed25519\ *|ssh-rsa\ *) ;;
            AGE-SECRET-KEY-*)
                bk_err "a PRIVATE age key was supplied as a backup recipient; the host must hold only the public key"
                exit 1 ;;
            *)
                bk_err "not an age/SSH public key in the backup recipient list: '$(echo "$line" | cut -c1-12)...'"
                exit 1 ;;
        esac
    done < "$out"
    exit 0
)

# Fail-closed gate, run before anything is dumped. Prints nothing on success.
bk_require_encryption_config() (
    mode=$(bk_encryption_mode) || exit 1
    if [ "$mode" = none ]; then
        exit 0
    fi
    if ! command -v age >/dev/null 2>&1; then
        bk_err "BACKUP_ENCRYPTION=age but the 'age' binary is not installed"
        exit 1
    fi
    probe=$(mktemp) || exit 1
    if ! bk_write_recipients "$probe"; then
        rm -f "$probe"
        bk_err "BACKUP_ENCRYPTION=age but no valid recipient is configured (set BACKUP_AGE_RECIPIENT or put public keys in BACKUP_AGE_RECIPIENTS_FILE=${BACKUP_AGE_RECIPIENTS_FILE:-<unset>}). Refusing to write an unencrypted backup. Set BACKUP_ENCRYPTION=none only if you accept plaintext backups on this host (they are never shipped offsite)."
        exit 1
    fi
    rm -f "$probe"
    exit 0
)

# ---------------------------------------------------------------------------------------------
# Manifests
# ---------------------------------------------------------------------------------------------

# Relative paths of every regular file under $1, sorted, excluding names in $2..$n.
bk_list_files() (
    root=$1
    shift
    (
        cd "$root" || exit 1
        find . -type f | sed 's|^\./||' | while IFS= read -r f; do
            skip=0
            for ex in "$@"; do
                [ "$f" = "$ex" ] && skip=1
            done
            if [ "$skip" -eq 0 ]; then echo "$f"; fi
        done | LC_ALL=C sort
    )
)

# sha256sum-format manifest of every file under $1 (except $3...) written to $2.
bk_write_manifest() (
    root=$1
    out=$2
    shift 2
    list=$(bk_list_files "$root" "$@") || exit 1
    : > "$out" || exit 1
    if [ -z "$list" ]; then
        exit 0
    fi
    (
        cd "$root" || exit 1
        echo "$list" | while IFS= read -r f; do
            sha256sum "$f"
        done
    ) >> "$out" || exit 1
)

# Checks manifest $2 (relative to directory $1) is complete AND correct: every listed file
# matches its hash, and no unlisted file exists except the names in $3...
bk_check_manifest() (
    root=$1
    manifest=$2
    shift 2
    [ -s "$manifest" ] || { bk_err "manifest $manifest is missing or empty"; exit 1; }
    if ! (cd "$root" && sha256sum -c "$manifest" >/dev/null 2>&1); then
        bk_err "checksum mismatch or missing file under $root (manifest $(basename "$manifest"))"
        (cd "$root" && sha256sum -c "$manifest" 2>&1 | grep -v ': OK$' | head -n 5) >&2 || true
        exit 1
    fi
    listed=$(sed 's/^[0-9a-f]*[ *][ *]*//' "$manifest" | LC_ALL=C sort)
    present=$(bk_list_files "$root" "$@") || exit 1
    if [ "$listed" != "$present" ]; then
        bk_err "file set under $root does not match its manifest (unlisted or extra files present)"
        exit 1
    fi
    exit 0
)

# ---------------------------------------------------------------------------------------------
# Sealing and opening a set
# ---------------------------------------------------------------------------------------------

bk_is_set_name() (
    echo "$1" | grep -Eq "^${BK_SET_PREFIX}[0-9]{8}T[0-9]{6}Z\$"
)

# bk_seal_set <plaintext-staging-dir> <final-set-dir>
# Encrypts (or, in mode none, moves) every staged artifact into a sibling .incoming directory,
# writes both manifests, verifies the result, then renames it into place. The final directory
# either appears complete and verified or not at all.
bk_seal_set() (
    staging=$1
    final=$2
    mode=$(bk_encryption_mode) || exit 1
    parent=$(dirname "$final")
    incoming="$parent/.incoming-$(basename "$final")"
    rm -rf "$incoming"
    mkdir -p "$incoming" || exit 1

    bk_write_manifest "$staging" "$incoming/.plain-manifest" || exit 1
    if [ ! -s "$incoming/.plain-manifest" ]; then
        bk_err "nothing staged in $staging to seal"
        rm -rf "$incoming"
        exit 1
    fi

    printf 'aura-backup-set 1\nencryption=%s\n' "$mode" > "$incoming/$BK_FORMAT_FILE"

    if [ "$mode" = age ]; then
        recipients="$incoming/.recipients"
        bk_write_recipients "$recipients" || { bk_err "no usable age recipient"; rm -rf "$incoming"; exit 1; }
        files=$(bk_list_files "$staging") || exit 1
        echo "$files" | while IFS= read -r f; do
            mkdir -p "$incoming/$(dirname "$f")" || exit 1
            age -e -R "$recipients" -o "$incoming/$f.age" "$staging/$f" || exit 1
        done || { bk_err "age encryption failed"; rm -rf "$incoming"; exit 1; }
        age -e -R "$recipients" -o "$incoming/$BK_MANIFEST.age" "$incoming/.plain-manifest" \
            || { bk_err "age encryption of manifest failed"; rm -rf "$incoming"; exit 1; }
        rm -f "$recipients" "$incoming/.plain-manifest"
    else
        files=$(bk_list_files "$staging") || exit 1
        echo "$files" | while IFS= read -r f; do
            mkdir -p "$incoming/$(dirname "$f")" || exit 1
            mv "$staging/$f" "$incoming/$f" || exit 1
        done || { rm -rf "$incoming"; exit 1; }
        mv "$incoming/.plain-manifest" "$incoming/$BK_MANIFEST"
    fi

    bk_write_manifest "$incoming" "$incoming/$BK_CHECKSUMS" "$BK_CHECKSUMS" || { rm -rf "$incoming"; exit 1; }
    if ! bk_verify_set "$incoming"; then
        rm -rf "$incoming"
        exit 1
    fi
    rm -rf "$final"
    mv "$incoming" "$final" || exit 1
)

# Integrity check that needs no key: CHECKSUMS.sha256 covers every stored file.
bk_verify_set() (
    set_dir=$1
    [ -d "$set_dir" ] || { bk_err "no such backup set: $set_dir"; exit 1; }
    [ -f "$set_dir/$BK_FORMAT_FILE" ] || { bk_err "$set_dir has no $BK_FORMAT_FILE; not a backup set"; exit 1; }
    bk_check_manifest "$set_dir" "$set_dir/$BK_CHECKSUMS" "$BK_CHECKSUMS"
)

bk_set_encryption() (
    sed -n 's/^encryption=//p' "$1/$BK_FORMAT_FILE"
)

# bk_open_set <set-dir> <output-dir>
# Verifies CHECKSUMS, decrypts every artifact into <output-dir> with BACKUP_AGE_IDENTITY_FILE,
# then verifies the decrypted plaintext against the (decrypted) MANIFEST. Refuses on any
# mismatch and removes what it wrote. <output-dir> holds PLAINTEXT afterwards.
bk_open_set() (
    set_dir=$1
    out=$2
    bk_verify_set "$set_dir" || exit 1
    mkdir -p "$out" || exit 1
    enc=$(bk_set_encryption "$set_dir")
    case "$enc" in
        age)
            identity=${BACKUP_AGE_IDENTITY_FILE:-}
            if [ -z "$identity" ] || [ ! -f "$identity" ]; then
                bk_err "set is age-encrypted: set BACKUP_AGE_IDENTITY_FILE to the private key file (kept offline)"
                exit 1
            fi
            command -v age >/dev/null 2>&1 || { bk_err "the 'age' binary is required to decrypt"; exit 1; }
            age -d -i "$identity" -o "$out/$BK_MANIFEST" "$set_dir/$BK_MANIFEST.age" \
                || { bk_err "cannot decrypt the manifest (wrong key, or tampered ciphertext)"; rm -f "$out/$BK_MANIFEST"; exit 1; }
            files=$(bk_list_files "$set_dir" "$BK_CHECKSUMS" "$BK_FORMAT_FILE" "$BK_MANIFEST.age") || exit 1
            echo "$files" | while IFS= read -r f; do
                case "$f" in *.age) ;; *) bk_err "unexpected unencrypted file in an age set: $f"; exit 1 ;; esac
                plain=${f%.age}
                mkdir -p "$out/$(dirname "$plain")" || exit 1
                age -d -i "$identity" -o "$out/$plain" "$set_dir/$f" || exit 1
            done || { bk_err "decryption failed"; bk_wipe_opened "$out"; exit 1; }
            ;;
        none)
            log "WARNING: $set_dir is an UNENCRYPTED set"
            cp -R "$set_dir/." "$out/" || exit 1
            rm -f "$out/$BK_CHECKSUMS" "$out/$BK_FORMAT_FILE"
            ;;
        *)
            bk_err "unknown encryption '$enc' in $set_dir/$BK_FORMAT_FILE"
            exit 1 ;;
    esac
    if ! bk_check_manifest "$out" "$out/$BK_MANIFEST" "$BK_MANIFEST"; then
        bk_err "decrypted content does not match the set's manifest; refusing to use it"
        bk_wipe_opened "$out"
        exit 1
    fi
    exit 0
)

bk_wipe_opened() (
    [ -n "$1" ] && [ -d "$1" ] && rm -rf "${1:?}"/* "${1:?}"/.[!.]* 2>/dev/null
    exit 0
)

# ---------------------------------------------------------------------------------------------
# Retention (grandfather-father-son)
# ---------------------------------------------------------------------------------------------

# Reads set names on stdin; prints the ones the policy does NOT keep. Names that are not
# well-formed set names are ignored (never printed, so never deleted). The newest set is always
# kept, whatever the policy says.
#
#   daily   = keep the newest set of each of the last N distinct days that have one
#   weekly  = ... of each of the last N distinct weeks (Monday-based)
#   monthly = ... of each of the last N distinct calendar months
#
# A set is kept if any rule keeps it (the restic/borg semantics).
bk_retention_prune_list() (
    keep_daily=${1:-7}
    keep_weekly=${2:-4}
    keep_monthly=${3:-6}
    grep -E "^${BK_SET_PREFIX}[0-9]{8}T[0-9]{6}Z\$" | LC_ALL=C sort -r -u | awk \
        -v kd="$keep_daily" -v kw="$keep_weekly" -v km="$keep_monthly" -v pfx="$BK_SET_PREFIX" '
        function days_from_civil(y, m, d,    era, yoe, doy, doe) {
            y -= (m <= 2)
            era = int(y / 400)
            yoe = y - era * 400
            doy = int((153 * (m + (m > 2 ? -3 : 9)) + 2) / 5) + d - 1
            doe = yoe * 365 + int(yoe / 4) - int(yoe / 100) + doy
            return era * 146097 + doe - 719468
        }
        {
            s = substr($0, length(pfx) + 1)
            y = substr(s, 1, 4) + 0; m = substr(s, 5, 2) + 0; d = substr(s, 7, 2) + 0
            day = substr(s, 1, 8)
            month = substr(s, 1, 6)
            n = days_from_civil(y, m, d)
            week = n - ((n + 3) % 7)          # the Monday of that week (1970-01-01 was a Thursday)
            keep = (NR == 1)
            if (day != last_day)     { if (cd < kd) keep = 1; cd++; last_day = day }
            if (week != last_week)   { if (cw < kw) keep = 1; cw++; last_week = week }
            if (month != last_month) { if (cm < km) keep = 1; cm++; last_month = month }
            if (!keep) print $0
        }'
)

# bk_prune_local <backup-dir> <keep_daily> <keep_weekly> <keep_monthly>
# Never deletes anything unless the newest local set verifies -- if the most recent backup is
# bad, the older ones may be the only good copies left.
bk_prune_local() (
    dir=$1
    names=$(cd "$dir" && for d in "${BK_SET_PREFIX}"*; do
        if [ -d "$d" ] && bk_is_set_name "$d"; then echo "$d"; fi
    done | LC_ALL=C sort -r)
    newest=$(echo "$names" | head -n 1)
    if [ -z "$newest" ]; then
        log "retention: no backup sets present; nothing to prune"
        exit 0
    fi
    if ! bk_verify_set "$dir/$newest" >/dev/null 2>&1; then
        bk_err "retention: newest set $newest does not verify; NOT pruning anything"
        exit 1
    fi
    echo "$names" | bk_retention_prune_list "$2" "$3" "$4" | while IFS= read -r victim; do
        [ -n "$victim" ] || continue
        log "retention: removing local set $victim"
        rm -rf "${dir:?}/$victim"
    done
    exit 0
)

# ---------------------------------------------------------------------------------------------
# Offsite (rclone)
# ---------------------------------------------------------------------------------------------

# bk_offsite_push <set-dir> <remote-base>
# Uploads one set and verifies the remote copy before reporting success:
#   1. rclone check --one-way: every local file exists remotely with the same size, and the same
#      hash wherever local and remote share a hash type (S3 multipart objects may only have size);
#   2. the remote CHECKSUMS.sha256 is fetched and compared byte-for-byte with the local one;
#   3. BACKUP_OFFSITE_VERIFY=download additionally re-reads every remote object (rclone check
#      --download) -- slower, but does not depend on the backend exposing hashes.
bk_offsite_push() (
    set_dir=$1
    remote="$2/$(basename "$set_dir")"
    if [ "$(bk_set_encryption "$set_dir")" != age ]; then
        bk_err "refusing to ship an unencrypted backup set offsite ($set_dir)"
        exit 1
    fi
    log "offsite: rclone copy $(basename "$set_dir") -> $remote"
    rclone copy "$set_dir" "$remote" || { bk_err "rclone copy failed"; exit 1; }
    check_flags="--one-way"
    if [ "${BACKUP_OFFSITE_VERIFY:-check}" = download ]; then
        check_flags="--one-way --download"
    fi
    # shellcheck disable=SC2086 # check_flags is a deliberate word list
    if ! rclone check "$set_dir" "$remote" $check_flags; then
        bk_err "offsite verification failed: remote copy of $(basename "$set_dir") differs from local"
        exit 1
    fi
    remote_sums=$(rclone cat "$remote/$BK_CHECKSUMS") || { bk_err "cannot read remote $BK_CHECKSUMS"; exit 1; }
    if [ "$remote_sums" != "$(cat "$set_dir/$BK_CHECKSUMS")" ]; then
        bk_err "remote $BK_CHECKSUMS differs from local"
        exit 1
    fi
    log "offsite: verified $(basename "$set_dir") at $remote"
    exit 0
)

# bk_offsite_prune <remote-base> <verified-set-name> <kd> <kw> <km>
# Applies the same GFS policy to remote sets -- but only when the newest remote set is the one
# this cycle just uploaded AND verified. Anything else means the remote holds something this
# host did not produce or could not verify, and deleting around it is not safe.
bk_offsite_prune() (
    base=$1
    verified=$2
    listing=$(rclone lsf --dirs-only "$base") || { bk_err "cannot list $base"; exit 1; }
    names=$(echo "$listing" | sed 's|/$||' | while IFS= read -r d; do
        if bk_is_set_name "$d"; then echo "$d"; fi
    done | LC_ALL=C sort -r)
    newest=$(echo "$names" | head -n 1)
    if [ "$newest" != "$verified" ]; then
        bk_err "offsite retention: newest remote set is '$newest', not the just-verified '$verified'; NOT pruning"
        exit 1
    fi
    echo "$names" | bk_retention_prune_list "$3" "$4" "$5" | while IFS= read -r victim; do
        [ -n "$victim" ] || continue
        log "offsite retention: removing $base/$victim"
        rclone purge "$base/$victim" || bk_err "could not remove remote $victim"
    done
    exit 0
)
