/// The public half of the key that signs `desktop-update.json` (F18).
///
/// `desktop-package.yml` signs the manifest bytes with the private half (a repository secret,
/// `scripts/desktop_update_manifest.py`) and publishes the signature next to it as
/// `desktop-update.json.sig`: Ed25519 over the exact manifest bytes, base64 (standard alphabet).
/// The app refuses any update whose manifest does not verify against this key -- so a release
/// whose files and checksums were swapped together, which the SHA-256 alone cannot catch, is not
/// installed.
///
/// **Until the owner generates the keypair this is a placeholder, and updates fail closed**: no
/// update is offered, and the check logs why. To set it: generate an Ed25519 keypair, store the
/// private key as the workflow's signing secret, and put the raw 32-byte public key here, base64.
/// Rotating it means shipping a build with the new key before signing with the new private key.
library;

/// Not a key: verification fails closed while this is the configured value.
const String kUpdateSigningKeyPlaceholder = 'UNSET-generate-an-ed25519-keypair-and-paste-the-public-key';

/// Base64 (standard alphabet) of the raw 32-byte Ed25519 public key.
const String kUpdateSigningPublicKeyBase64 = kUpdateSigningKeyPlaceholder;
