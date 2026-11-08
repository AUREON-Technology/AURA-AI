# Desktop releases, updates and rollback

How AURA Desktop is released, how installed copies update, and how to roll a bad release back
(M8.10). Built by `.github/workflows/desktop-package.yml`; the app side is
`aura_client/lib/desktop/update/`.

## Cutting a release

1. Bump `version:` in `aura_client/pubspec.yaml` (for example `3.0.2`) and merge it.
2. Tag that commit `desktop-v3.0.2` and push the tag. The tag must equal `desktop-v` plus the
   pubspec version; `scripts/desktop_update_manifest.py` refuses anything else, so a mislabelled
   tag cannot become what installed copies update to.
3. The workflow builds the Windows installer and the Linux tar.gz, each with `aura_hostd`
   bundled, and a `.sha256` for each. It then publishes a GitHub Release named
   `AURA Desktop 3.0.2`, marked **latest**, with those files and `desktop-update.json`.

Each build knows its own version and where to look for updates, through `--dart-define` values the
workflow sets. A build from source has neither, so it never offers an update.

## How installed copies update

- On launch, the IDE fetches `desktop-update.json` from the latest release. An unreachable channel
  only means no prompt is shown.
- If the release is newer, the status bar shows **Update to 3.0.2**. Nothing downloads until the
  person clicks it and confirms.
- **Windows:** the installer downloads to a temporary folder. It runs only if its SHA-256 matches
  the manifest; a mismatched file is deleted. Unsaved editor buffers are flushed to the hot-exit
  backup first. The app then starts the installer silently and quits. The installer replaces the
  files and reopens the app, which restores the unsaved buffers.
- **Linux:** the item opens the tar.gz download in the browser. There is no in-app install.

The checksum catches a corrupt or substituted download. It does **not** protect against a
compromised release, because the file and its hash come from the same place. Authenticode signing
is what does that. The workflow signs as soon as the `AURA_WINDOWS_SIGNING_CERT` and
`AURA_WINDOWS_SIGNING_PASSWORD` secrets exist.

## Rolling back a bad release

Run the **Desktop Package** workflow manually with `rollback_to` set to the last good tag, for
example `desktop-v3.0.1`. It builds nothing. Instead it:

1. downloads that release's `desktop-update.json`, sets `"rollback": true`, and re-uploads it;
2. marks that release **latest** again.

Installed copies on the bad version then see an older version flagged as a rollback, and the status
bar shows **Roll back to 3.0.1**. An older version is never offered without that flag. Inno Setup
installs over the newer version.

To move forward again, cut a new, higher release (`desktop-v3.0.3`). It becomes latest with
`"rollback": false`. The rolled-back release keeps its flag, which matters only if it is ever
made latest again by hand.

## Not verified yet

- **The publish and rollback jobs** run only on a tag or a manual dispatch, so no pull request
  exercises them. The first real `desktop-v*` tag is their first run.
- **An update or rollback on a real Windows machine.** The update check, checksum verification
  and the installer launch are unit-tested, and the installer is built in CI, but no installed
  copy has updated itself yet.
