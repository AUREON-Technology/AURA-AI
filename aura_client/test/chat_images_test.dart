import 'dart:convert';
import 'dart:typed_data';

import 'package:aura_client/core/ai/online_agents/sse_agent_client.dart';
import 'package:aura_client/core/ai/vision/chat_images.dart';
import 'package:aura_client/core/auth/token_manager.dart';
import 'package:aura_client/core/design_system/aura_theme_v2.dart';
import 'package:aura_client/features/chat/presentation/chat_view_model.dart';
import 'package:aura_client/features/chat/presentation/widgets/chat_image_attachments.dart';
import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

class _StubTokenManager extends TokenManager {
  _StubTokenManager(super.ref);

  @override
  Future<String?> getValidAccessToken() async => 'jwt-test';
}

/// A real 1x1 PNG, so `Image.memory` can decode it in widget tests.
final _png = base64Decode(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==',
);

ChatImageAttachment _image(String name, [Uint8List? bytes]) =>
    ChatImageAttachment(bytes: bytes ?? _png, name: name);

const _on = VisionCapabilities(
  enabled: true, available: true, maxImages: 2, maxImageBytes: 1024 * 1024);

Widget _wrap(Widget child, {required VisionCapabilities capabilities}) {
  return ProviderScope(
    overrides: [
      visionCapabilitiesProvider.overrideWith((ref) async => capabilities),
    ],
    child: MaterialApp(
      theme: AuraThemeV2.light,
      home: Scaffold(
        body: Column(children: [child, const PendingImagesStrip(enabled: true)]),
      ),
    ),
  );
}

void main() {
  group('request body', () {
    test('a text turn has no images key at all', () {
      final body = chatStreamBody(message: 'hi', sessionId: 's', agentId: 'orchestrator');
      expect(body.containsKey('images'), isFalse);
    });

    test('a turn with no workspace root has no workspace_root key at all', () {
      final body = chatStreamBody(message: 'hi', sessionId: 's', agentId: 'engineer');
      expect(body.containsKey('workspace_root'), isFalse);
    });

    test("the desktop IDE's active root goes as workspace_root (FSA-006)", () {
      final body = chatStreamBody(
        message: 'run the tests',
        sessionId: 's',
        agentId: 'engineer',
        workspaceRoot: '/home/me/projects/aura',
      );
      expect(body['workspace_root'], '/home/me/projects/aura');
    });

    test('images go as base64 with the name-implied type', () {
      final body = chatStreamBody(
        message: 'what is this?',
        sessionId: 's',
        agentId: 'orchestrator',
        images: [_image('shot.PNG'), _image('photo.jpeg')],
      );
      expect(body['images'], [
        {'media_type': 'image/png', 'data_base64': base64Encode(_png)},
        {'media_type': 'image/jpeg', 'data_base64': base64Encode(_png)},
      ]);
      expect(jsonEncode(body), isA<String>());
    });

    test('an unknown extension sends no type claim and lets the server sniff', () {
      expect(_image('scan.heic').mediaType, '');
    });
  });

  group('capabilities', () {
    test('parse the gateway response', () {
      final caps = VisionCapabilities.fromJson({
        'vision': {'enabled': true, 'available': true, 'max_images': 4, 'max_image_bytes': 10},
      });
      expect(caps.canAttach, isTrue);
      expect(caps.maxImages, 4);
    });

    test('enabled without an available model offers nothing', () {
      final caps = VisionCapabilities.fromJson({
        'vision': {'enabled': true, 'available': false, 'max_images': 4},
      });
      expect(caps.canAttach, isFalse);
    });

    test('GET /api/v3/capabilities with the JWT; any failure means no images', () async {
      http.Request? recorded;
      final ok = MockClient((request) async {
        recorded = request;
        return http.Response(
          jsonEncode({'vision': {'enabled': true, 'available': true, 'max_images': 4, 'max_image_bytes': 99}}),
          200,
        );
      });
      final container = ProviderContainer(overrides: [
        tokenManagerProvider.overrideWith((ref) => _StubTokenManager(ref)),
        capabilitiesClientProvider.overrideWith((ref) => CapabilitiesClient(ref, httpClient: ok)),
      ]);
      addTearDown(container.dispose);

      final caps = await container.read(capabilitiesClientProvider).vision();

      expect(caps.canAttach, isTrue);
      expect(recorded!.url.path, '/api/v3/capabilities');
      expect(recorded!.headers['Authorization'], 'Bearer jwt-test');

      final old = ProviderContainer(overrides: [
        tokenManagerProvider.overrideWith((ref) => _StubTokenManager(ref)),
        capabilitiesClientProvider.overrideWith((ref) => CapabilitiesClient(
              ref,
              httpClient: MockClient((_) async => http.Response('{"detail":"Not Found"}', 404)),
            )),
      ]);
      addTearDown(old.dispose);
      expect((await old.read(capabilitiesClientProvider).vision()).canAttach, isFalse);
    });
  });

  group('pending images', () {
    test('refuse past the count and the size limit', () {
      final container = ProviderContainer();
      addTearDown(container.dispose);
      final pending = container.read(pendingChatImagesProvider.notifier);

      expect(pending.add(_image('a.png'), _on), isNull);
      expect(pending.add(_image('b.png', Uint8List(2 * 1024 * 1024)), _on), AttachRejection.tooLarge);
      expect(pending.add(_image('c.png'), _on), isNull);
      expect(pending.add(_image('d.png'), _on), AttachRejection.tooMany);
      expect(container.read(pendingChatImagesProvider).map((i) => i.name), ['a.png', 'c.png']);

      pending.removeAt(0);
      expect(container.read(pendingChatImagesProvider).single.name, 'c.png');
    });

    test('an image alone is enough to send', () {
      const state = ChatUiState();
      expect(state.canSend('  ', isStreaming: false), isFalse);
      expect(state.canSend('  ', isStreaming: false, hasImages: true), isTrue);
      expect(state.canSend('', isStreaming: true, hasImages: true), isFalse);
    });

    test('the transcript note mirrors the server marker', () {
      expect(imagesAttachedNote(1), '[1 image attached]');
      expect(imagesAttachedNote(3), '[3 images attached]');
    });
  });

  group('AttachImageButton', () {
    testWidgets('is not offered when the gateway cannot understand images', (tester) async {
      await tester.pumpWidget(_wrap(
        const AttachImageButton(enabled: true),
        capabilities: VisionCapabilities.unavailable,
      ));
      await tester.pumpAndSettle();
      expect(find.byTooltip('Attach image'), findsNothing);
    });

    testWidgets('attaches picked images, shows labelled thumbnails, and removes them',
        (tester) async {
      await tester.pumpWidget(_wrap(
        AttachImageButton(
          enabled: true,
          pickFiles: () async => [
            PlatformFile(name: 'receipt.png', size: _png.length, bytes: _png),
          ],
        ),
        capabilities: _on,
      ));
      await tester.pumpAndSettle();

      await tester.tap(find.byTooltip('Attach image'));
      await tester.pumpAndSettle();

      expect(find.bySemanticsLabel('Attached image 1 of 1: receipt.png'), findsOneWidget);
      await tester.tap(find.byTooltip('Remove image 1'));
      await tester.pumpAndSettle();
      expect(find.bySemanticsLabel(RegExp('Attached image')), findsNothing);
    });

    testWidgets('explains a refused attachment instead of dropping it silently', (tester) async {
      await tester.pumpWidget(_wrap(
        AttachImageButton(
          enabled: true,
          pickFiles: () async => [
            for (final n in ['a.png', 'b.png', 'c.png'])
              PlatformFile(name: n, size: _png.length, bytes: _png),
          ],
        ),
        capabilities: _on,
      ));
      await tester.pumpAndSettle();

      await tester.tap(find.byTooltip('Attach image'));
      await tester.pumpAndSettle();

      expect(find.text('You can attach up to 2 images to one message.'), findsOneWidget);
      expect(find.byTooltip('Image limit reached'), findsOneWidget);
    });
  });
}
