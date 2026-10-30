import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;

import '../../auth/token_manager.dart';
import '../../config/backend_url_provider.dart';
import '../../security/device_validation_service.dart';

/// An image the user attached to the next chat turn (ADR-031).
///
/// Held in memory only: it is sent once as base64 on `/api/v3/chat/stream` and never written to
/// Drift or synced, matching the server, which keeps neither the bytes nor the description.
@immutable
class ChatImageAttachment {
  final Uint8List bytes;
  final String name;

  const ChatImageAttachment({required this.bytes, required this.name});

  /// The type implied by the file name. Only a hint: the server decides by the bytes.
  String get mediaType {
    final lower = name.toLowerCase();
    if (lower.endsWith('.png')) return 'image/png';
    if (lower.endsWith('.webp')) return 'image/webp';
    if (lower.endsWith('.jpg') || lower.endsWith('.jpeg')) return 'image/jpeg';
    return '';
  }

  Map<String, dynamic> toWire() => {
        'media_type': mediaType,
        'data_base64': base64Encode(bytes),
      };
}

/// The extensions the composer offers. The server accepts exactly JPEG, PNG and WebP.
const kChatImageExtensions = ['jpg', 'jpeg', 'png', 'webp'];

/// What `GET /api/v3/capabilities` says about images on this deployment.
@immutable
class VisionCapabilities {
  final bool enabled;
  final bool available;
  final int maxImages;
  final int maxImageBytes;

  const VisionCapabilities({
    required this.enabled,
    required this.available,
    required this.maxImages,
    required this.maxImageBytes,
  });

  /// What a client assumes when it cannot ask: no images. An old gateway without the route
  /// would silently drop `images` from the request, so offering them there would mislead.
  static const unavailable =
      VisionCapabilities(enabled: false, available: false, maxImages: 0, maxImageBytes: 0);

  factory VisionCapabilities.fromJson(Map<String, dynamic> json) {
    final vision = json['vision'];
    if (vision is! Map<String, dynamic>) return unavailable;
    return VisionCapabilities(
      enabled: vision['enabled'] == true,
      available: vision['available'] == true,
      maxImages: (vision['max_images'] as num?)?.toInt() ?? 0,
      maxImageBytes: (vision['max_image_bytes'] as num?)?.toInt() ?? 0,
    );
  }

  bool get canAttach => enabled && available && maxImages > 0;
}

/// Thin client for `GET /api/v3/capabilities`, with the same JWT and attestation headers as
/// every other v3 client.
class CapabilitiesClient {
  final Ref _ref;
  final http.Client? _http;

  CapabilitiesClient(this._ref, {http.Client? httpClient}) : _http = httpClient;

  Future<VisionCapabilities> vision() async {
    final backendUrl = _ref.read(backendUrlProvider);
    try {
      final token = await _ref.read(tokenManagerProvider).getValidAccessToken();
      final headers = <String, String>{
        if (token != null) 'Authorization': 'Bearer $token',
        ...await DeviceValidationService.getAttestationHeaders(backendUrl: backendUrl),
      };
      final uri = Uri.parse('$backendUrl/api/v3/capabilities');
      final client = _http;
      final response = await (client != null
              ? client.get(uri, headers: headers)
              : http.get(uri, headers: headers))
          .timeout(const Duration(seconds: 10));
      if (response.statusCode != 200) return VisionCapabilities.unavailable;
      final decoded = jsonDecode(response.body);
      return decoded is Map<String, dynamic>
          ? VisionCapabilities.fromJson(decoded)
          : VisionCapabilities.unavailable;
    } catch (e) {
      debugPrint('[CapabilitiesClient] capabilities unavailable: $e');
      return VisionCapabilities.unavailable;
    }
  }
}

final capabilitiesClientProvider = Provider<CapabilitiesClient>((ref) => CapabilitiesClient(ref));

final visionCapabilitiesProvider = FutureProvider<VisionCapabilities>(
  (ref) => ref.read(capabilitiesClientProvider).vision(),
);

/// Why an attachment was not added, for the composer to explain.
enum AttachRejection { tooMany, tooLarge }

/// Images waiting to go with the next message.
class PendingChatImages extends Notifier<List<ChatImageAttachment>> {
  @override
  List<ChatImageAttachment> build() => const [];

  /// Adds [image] unless it breaks [limits]; returns why not, or null when added.
  AttachRejection? add(ChatImageAttachment image, VisionCapabilities limits) {
    if (state.length >= limits.maxImages) return AttachRejection.tooMany;
    if (limits.maxImageBytes > 0 && image.bytes.length > limits.maxImageBytes) {
      return AttachRejection.tooLarge;
    }
    state = [...state, image];
    return null;
  }

  void removeAt(int index) {
    state = [...state]..removeAt(index);
  }

  void clear() => state = const [];
}

final pendingChatImagesProvider =
    NotifierProvider<PendingChatImages, List<ChatImageAttachment>>(PendingChatImages.new);

/// The note a user turn carries in the local transcript, mirroring the server's history marker.
String imagesAttachedNote(int count) =>
    count == 1 ? '[1 image attached]' : '[$count images attached]';

/// Images of messages already sent, for their bubbles' thumbnails (ADR-031 P5-4).
///
/// Memory only, for this app session: the bytes are never written to Drift or synced, the same
/// rule the server keeps. After a restart, or on another device, a bubble has only the
/// [imagesAttachedNote] marker in its text, and shows a chip instead of thumbnails. Bounded to
/// the [kSentImageMessages] most recent messages so a long session cannot hold every photo.
class SentChatImages extends Notifier<Map<String, List<ChatImageAttachment>>> {
  @override
  Map<String, List<ChatImageAttachment>> build() => const {};

  void remember(String messageId, List<ChatImageAttachment> images) {
    if (images.isEmpty) return;
    final next = Map<String, List<ChatImageAttachment>>.of(state)
      ..remove(messageId)
      ..[messageId] = List.unmodifiable(images);
    while (next.length > kSentImageMessages) {
      next.remove(next.keys.first);
    }
    state = next;
  }
}

const kSentImageMessages = 30;

final sentChatImagesProvider =
    NotifierProvider<SentChatImages, Map<String, List<ChatImageAttachment>>>(SentChatImages.new);

final _imagesNote = RegExp(r'\n?\[(\d+) images? attached\]\s*$');

/// Splits a stored user message into its text and the number of images its marker records.
({String text, int imageCount}) splitImagesNote(String content) {
  final match = _imagesNote.firstMatch(content);
  if (match == null) return (text: content, imageCount: 0);
  return (text: content.substring(0, match.start), imageCount: int.parse(match.group(1)!));
}
