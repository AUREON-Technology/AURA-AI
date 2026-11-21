# Desktop releases, updates and rollback

How AURA Desktop is released, how installed copies update, and how to roll a bad release back
(M8.10). Built by `.github/workflows/desktop-package.yml`; the app side is
`aura_client/lib/desktop/update/`.

## Always-latest setup (the `main` channel)

For people who just want the newest app, not a numbered release, and who should not need any
access to the repository. Built and published by `.github/workflows/desktop-main-channel.yml`.

```
push to main --> Windows runner builds the installer --> railway up (AURA-Web service)
                                                    --> https://web.aureon-ai.tech/desktop-ide/install
```

- **What people do:** open the install page, download `AURA-Desktop-Setup.cmd` (or the `.exe`
  installer directly) and run it. The setup file is a launcher, not a snapshot: each run reads
  `latest.json` from the website, downloads the newest installer from there, checks its SHA-256
  and runs it. A copy of the file that is months old still installs today's app, and running it
  again updates an existing install. Options: `-Silent`, `-Force` (reinstall even if current),
  `-DownloadOnly` (save the installer, do not run it). No GitHub account is involved.
- **What you do:** nothing beyond merging. Every push to `main` that touches `aura_client/`,
  `aura_hostd/` or `aura_web/` builds the installer and deploys it. Builds are queued, not
  cancelled. A deploy replaces the installer and `latest.json` together, because both are copied
  into the same image, so the page never offers a manifest and an installer that disagree.
- **Why the build is not on Railway:** Railway runs Linux containers, and Flutter cannot
  cross-build a Windows app from Linux. The build runs on a Windows runner (the same packaging as
  `desktop-package.yml`: `aura_hostd` bundled, Inno Setup installer); Railway only hosts the result.
  `scripts/deploy_client_desktop.ps1` alone produces a bare app folder, not an installer.
- **The web service:** Railway service `AURA-Web` (nginx, `aura_web/`), custom domain
  `web.aureon-ai.tech`. It serves exactly three downloads under `/desktop-ide/download/`:
  `latest.json` (commit, version, build time, installer name/size/SHA-256),
  `aura-desktop-windows-x64-setup.exe` and `AURA-Desktop-Setup.cmd`. Before the first build has
  been deployed the page says no build is published and the setup file stops with the same message.
- **One-time setup (two parts, both in GitHub Settings):** create the environment `web-production`
  and restrict its deployment branches to `main`; then store a Railway **project token** for the
  `production` environment as the secret `RAILWAY_WEB_TOKEN` **of that environment**, not as a
  repository secret (a repository secret is readable by a workflow on any branch; an environment
  secret only by jobs that passed the branch rule). Optionally add required reviewers to approve
  every deploy. Without the token `deploy-web` fails with a message saying so; it never skips
  silently. The job then waits until the live site reports the pushed commit and the installer's
  exact size, so a green run means the site really serves that build.
- **Safeguards:** only `main` deploys (`deploy-web` has `if: github.ref == 'refs/heads/main'`, and the
  environment rule enforces it, since anyone who can push a branch can edit the `if`). A manual
  dispatch from another branch builds but never deploys. The `backend_url` input must be an https
  address on `aureon-ai.tech`, because it is baked into a publicly published installer.
- **Manual deploy:** stage `aura_web/` and `scripts/AURA-Desktop-Setup.cmd` with the installer and
  `latest.json` in `aura_web/build-assets/`, then `railway up --service AURA-Web` from that folder.
  Never run `railway up` from the repository root: it holds credential files.
- **Versions:** "Apps & features" shows `3.0.1-main.<sha7>`. Inside the app the version is the
  plain pubspec one, so a main build is offered the next signed `desktop-v` release as usual.
- **Trust:** the checksum catches a corrupt download, not a compromised site (file and hash come
  from the same place); the setup file only talks https. Authenticode signing is what protects
  against a bad build, and the workflow signs once the `AURA_WINDOWS_SIGNING_*` secrets exist.
  Until then Windows shows an "unknown publisher" prompt. A main build is exactly as trustworthy as
  `main` itself: whoever can merge can publish.
- **Prerequisite:** if the Microsoft Visual C++ runtime is missing, the setup file installs it
  (one UAC prompt), after checking that the download is signed by Microsoft.
- **Windows x64 only.** Linux has the tar.gz from the tagged releases.
- **Not verified yet:** the workflow has not run, because it needs a push to `main` and the
  `RAILWAY_WEB_TOKEN` secret. The serving path was tested on a throwaway Railway service with a
  stand-in installer (setup file end to end over https, page, headers, range requests); a real
  installer build and the missing-Visual-C++ path have not run.

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
