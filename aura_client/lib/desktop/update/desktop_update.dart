/// Desktop updates with rollback (M8.10).
///
/// Releases are GitHub Releases on this repository, published by `desktop-package.yml` for a
/// `desktop-v<version>` tag. Each carries the installer, the Linux bundle, their checksums and a
/// `desktop-update.json` manifest (written by `scripts/desktop_update_manifest.py`; the two sides
/// share one format and both are tested against it). The app fetches the manifest from the
/// release GitHub marks "latest".
///
/// * **The person decides.** A check only finds out; nothing downloads or installs until they
///   choose to.
/// * **Authenticity (F18).** The manifest is signed: Ed25519 over its exact bytes, published next
///   to it as `desktop-update.json.sig` (base64). Nothing in the manifest is read until that
///   signature verifies against the public key compiled into the app
///   (`update_signing_key.dart`), so a release whose installer *and* checksum were swapped
///   together is refused. With no real key configured, updates fail closed.
/// * **Integrity.** The download is hashed as it streams and refused unless its SHA-256 matches
///   the (verified) manifest; a mismatched file is deleted, never run. Authenticode signing of the
///   installer itself is separate (the workflow signs once a certificate exists).
/// * **Rollback is a channel decision.** An older version is never offered, unless the manifest
///   says `"rollback": true` -- which the owner publishes by re-marking a previous release as
///   latest (`desktop-package.yml`'s `rollback_to`). Installed apps then offer that version as
///   the update, and Inno Setup installs over the newer one.
/// * **Off in development.** The packaging workflow bakes in [kAppVersion] and
///   [kUpdateManifestUrl]; a build from source has neither, so it never offers anything.
library;

import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:cryptography/cryptography.dart' as ed;
import 'package:http/http.dart' as http;

import 'update_signing_key.dart';

/// The version this build is, baked in by `desktop-package.yml`. Empty for a source build.
const String kAppVersion = String.fromEnvironment('AURA_APP_VERSION');

/// Where this build looks for `desktop-update.json`. Empty for a source build.
const String kUpdateManifestUrl = String.fromEnvironment('AURA_UPDATE_MANIFEST_URL');

/// The manifest key for this machine, or `null` where there is no installer to update with.
String? currentPlatformKey() {
  if (Platform.isWindows) return 'windows-x64';
  if (Platform.isLinux) return 'linux-x64';
  return null;
}

class UpdateFormatException implements Exception {
  UpdateFormatException(this.message);
  final String message;
  @override
  String toString() => 'UpdateFormatException: $message';
}

/// The manifest's signature is missing, malformed or wrong, or no signing key is configured: the
/// manifest is not trusted and nothing is offered.
class UpdateSignatureException implements Exception {
  UpdateSignatureException(this.message);
  final String message;
  @override
  String toString() => 'UpdateSignatureException: $message';
}

/// Where the manifest's signature is published: the manifest URL plus `.sig`.
Uri manifestSignatureUrl(String manifestUrl) => Uri.parse('$manifestUrl.sig');

/// Verifies [signatureText] (base64 of a 64-byte Ed25519 signature) over exactly [manifestBytes]
/// with [publicKeyBase64] (base64 of a raw 32-byte Ed25519 public key). Throws
/// [UpdateSignatureException] on anything short of a valid signature -- including a key that is
/// not configured.
Future<void> verifyManifestSignature(
  List<int> manifestBytes,
  String signatureText,
  String publicKeyBase64,
) async {
  final key = _decodeBase64(publicKeyBase64);
  if (key == null || key.length != 32) {
    throw UpdateSignatureException('no update signing key is configured in this build');
  }
  final signature = _decodeBase64(signatureText.trim());
  if (signature == null || signature.length != 64) {
    throw UpdateSignatureException('the manifest signature is malformed');
  }
  final bool valid;
  try {
    valid = await ed.Ed25519().verify(
      manifestBytes,
      signature: ed.Signature(
        signature,
        publicKey: ed.SimplePublicKey(key, type: ed.KeyPairType.ed25519),
      ),
    );
  } catch (e) {
    throw UpdateSignatureException('the manifest signature could not be checked: $e');
  }
  if (!valid) {
    throw UpdateSignatureException('the manifest signature does not match the release key');
  }
}

List<int>? _decodeBase64(String text) {
  if (text.isEmpty) return null;
  try {
    return base64.decode(text);
  } on FormatException {
    return null;
  }
}

class UpdateIntegrityException implements Exception {
  UpdateIntegrityException(this.message);
  final String message;
  @override
  String toString() => 'UpdateIntegrityException: $message';
}

/// A release version: `major.minor.patch`, compared numerically.
class AppVersion implements Comparable<AppVersion> {
  const AppVersion(this.major, this.minor, this.patch);

  /// `null` for anything that is not three dot-separated numbers (a `+build` suffix is ignored,
  /// as pubspec versions carry one).
  static AppVersion? tryParse(String raw) {
    final match = RegExp(r'^(\d+)\.(\d+)\.(\d+)(?:\+\S*)?$').firstMatch(raw.trim());
    if (match == null) return null;
    return AppVersion(int.parse(match[1]!), int.parse(match[2]!), int.parse(match[3]!));
  }

  final int major;
  final int minor;
  final int patch;

  @override
  int compareTo(AppVersion other) {
    if (major != other.major) return major.compareTo(other.major);
    if (minor != other.minor) return minor.compareTo(other.minor);
    return patch.compareTo(other.patch);
  }

  bool operator >(AppVersion other) => compareTo(other) > 0;
  bool operator <(AppVersion other) => compareTo(other) < 0;

  @override
  bool operator ==(Object other) => other is AppVersion && compareTo(other) == 0;

  @override
  int get hashCode => Object.hash(major, minor, patch);

  @override
  String toString() => '$major.$minor.$patch';
}

class UpdateAsset {
  const UpdateAsset({required this.url, required this.sha256, this.size});

  final Uri url;
  final String sha256;
  final int? size;
}

class UpdateManifest {
  const UpdateManifest({required this.version, required this.platforms, this.rollback = false});

  final AppVersion version;
  final Map<String, UpdateAsset> platforms;

  /// Set when the owner rolled the channel back to this (older) version on purpose.
  final bool rollback;

  static final _sha256 = RegExp(r'^[0-9a-f]{64}$');

  /// Parses `desktop-update.json`. Refuses anything the client would not act on safely: an
  /// unknown schema, a non-HTTPS asset URL, a malformed hash.
  factory UpdateManifest.fromJson(Map<String, dynamic> json) {
    if (json['schema'] != 1) throw UpdateFormatException('unsupported manifest schema ${json['schema']}');
    final version = AppVersion.tryParse('${json['version']}');
    if (version == null) throw UpdateFormatException('bad version ${json['version']}');
    final rawPlatforms = json['platforms'];
    if (rawPlatforms is! Map) throw UpdateFormatException('no platforms');
    final platforms = <String, UpdateAsset>{};
    for (final entry in rawPlatforms.entries) {
      final asset = entry.value;
      if (asset is! Map) throw UpdateFormatException('bad asset for ${entry.key}');
      final url = Uri.tryParse('${asset['url']}');
      if (url == null || url.scheme != 'https' || url.host.isEmpty) {
        throw UpdateFormatException('${entry.key}: asset URL must be https');
      }
      final hash = '${asset['sha256']}'.toLowerCase();
      if (!_sha256.hasMatch(hash)) throw UpdateFormatException('${entry.key}: bad sha256');
      platforms['${entry.key}'] = UpdateAsset(
        url: url,
        sha256: hash,
        size: asset['size'] is int ? asset['size'] as int : null,
      );
    }
    return UpdateManifest(version: version, platforms: platforms, rollback: json['rollback'] == true);
  }
}

/// An update this build should offer.
class AvailableUpdate {
  const AvailableUpdate({required this.version, required this.asset, required this.isRollback});

  final AppVersion version;
  final UpdateAsset asset;

  /// The channel was rolled back: this is an older version the owner wants everyone on.
  final bool isRollback;
}

/// Whether [manifest] has something for [current] on [platformKey]: a newer version, or an older
/// one only when the manifest is a deliberate rollback. The same version is never offered.
AvailableUpdate? decideUpdate(AppVersion current, UpdateManifest manifest, String platformKey) {
  final asset = manifest.platforms[platformKey];
  if (asset == null) return null;
  if (manifest.version > current) {
    return AvailableUpdate(version: manifest.version, asset: asset, isRollback: false);
  }
  if (manifest.rollback && manifest.version < current) {
    return AvailableUpdate(version: manifest.version, asset: asset, isRollback: true);
  }
  return null;
}

typedef DetachedLauncher = Future<void> Function(String executable, List<String> arguments);

/// Inno Setup flags for an update from inside the app: progress only, no questions, close the
/// running app and daemon (`CloseApplications=yes` in aura.iss), and relaunch afterwards (the
/// `skipifnotsilent` [Run] entry).
const List<String> kSilentInstallerArguments = [
  '/SILENT',
  '/SUPPRESSMSGBOXES',
  '/NORESTART',
  '/CLOSEAPPLICATIONS',
  '/SP-',
];

class DesktopUpdater {
  DesktopUpdater({
    http.Client? client,
    this.appVersion = kAppVersion,
    this.manifestUrl = kUpdateManifestUrl,
    String? platformKey,
    this.signingPublicKey = kUpdateSigningPublicKeyBase64,
    Directory Function()? downloadDir,
    DetachedLauncher? launch,
  })  : _client = client ?? http.Client(),
        _platformKey = platformKey ?? currentPlatformKey(),
        _downloadDir = downloadDir ?? (() => Directory.systemTemp),
        _launch = launch ??
            ((exe, args) async {
              await Process.start(exe, args, mode: ProcessStartMode.detached);
            });

  final String appVersion;
  final String manifestUrl;

  /// The pinned key the manifest must be signed with (base64, raw 32-byte Ed25519).
  final String signingPublicKey;
  final http.Client _client;
  final String? _platformKey;
  final Directory Function() _downloadDir;
  final DetachedLauncher _launch;

  /// Whether this build can update at all: it knows its version, where to look, and has an
  /// installer for this platform.
  bool get enabled =>
      AppVersion.tryParse(appVersion) != null && manifestUrl.startsWith('https://') && _platformKey != null;

  /// Checks the channel. `null` when there is nothing to offer, or updates are off. Throws
  /// [UpdateSignatureException] when the manifest is not signed by [signingPublicKey] (or no key
  /// is configured) -- the caller logs it and offers nothing.
  Future<AvailableUpdate?> check() async {
    if (!enabled) return null;
    // Fail closed before touching the network: without a key nothing could be trusted anyway.
    final key = _decodeBase64(signingPublicKey);
    if (key == null || key.length != 32) {
      throw UpdateSignatureException('no update signing key is configured in this build');
    }
    final response = await _client.get(Uri.parse(manifestUrl));
    if (response.statusCode != 200) {
      throw http.ClientException('update manifest returned ${response.statusCode}');
    }
    final signature = await _client.get(manifestSignatureUrl(manifestUrl));
    if (signature.statusCode != 200) {
      throw UpdateSignatureException('no manifest signature published (${signature.statusCode})');
    }
    // The exact bytes as downloaded: re-encoding parsed JSON would not be what was signed.
    final manifestBytes = response.bodyBytes;
    await verifyManifestSignature(manifestBytes, signature.body, signingPublicKey);
    final Object? decoded;
    try {
      decoded = jsonDecode(utf8.decode(manifestBytes));
    } on FormatException catch (e) {
      throw UpdateFormatException('manifest is not JSON: ${e.message}');
    }
    if (decoded is! Map<String, dynamic>) throw UpdateFormatException('manifest is not an object');
    final manifest = UpdateManifest.fromJson(decoded);
    return decideUpdate(AppVersion.tryParse(appVersion)!, manifest, _platformKey!);
  }

  /// Downloads [update]'s asset, hashing it as it streams, and returns the file only if its
  /// SHA-256 matches the manifest. A mismatch deletes the file and throws.
  Future<File> download(AvailableUpdate update, {void Function(int received, int? total)? onProgress}) async {
    final name = update.asset.url.pathSegments.isEmpty ? 'aura-update' : update.asset.url.pathSegments.last;
    final file = File('${_downloadDir().path}${Platform.pathSeparator}$name');
    final response = await _client.send(http.Request('GET', update.asset.url));
    if (response.statusCode != 200) {
      throw http.ClientException('update download returned ${response.statusCode}');
    }
    final sink = file.openWrite();
    final hashOutput = _Accumulator<Digest>();
    final hasher = sha256.startChunkedConversion(hashOutput);
    var received = 0;
    try {
      await for (final chunk in response.stream) {
        hasher.add(chunk);
        sink.add(chunk);
        received += chunk.length;
        onProgress?.call(received, response.contentLength ?? update.asset.size);
      }
    } finally {
      hasher.close();
      await sink.close();
    }
    final actual = hashOutput.events.single.toString();
    if (actual != update.asset.sha256) {
      await file.delete();
      throw UpdateIntegrityException('downloaded file does not match the release checksum');
    }
    return file;
  }

  /// Starts the Windows installer silently and detached. The caller quits the app afterwards so
  /// the installer can replace its files; the installer relaunches it when done.
  Future<void> install(File installer) async {
    if (_platformKey != 'windows-x64') {
      throw UnsupportedError('in-app install is Windows-only; Linux updates are a manual download');
    }
    await _launch(installer.path, kSilentInstallerArguments);
  }
}

/// Collects a chunked conversion's output (what `package:convert`'s `AccumulatorSink` does), kept
/// local so this file needs no dependency beyond `crypto`.
class _Accumulator<T> implements Sink<T> {
  final List<T> events = [];
  @override
  void add(T event) => events.add(event);
  @override
  void close() {}
}
