import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/ai/vision/chat_images.dart';
import 'package:aura_client/core/design_system/design_system.dart';

/// Picks images for the next message (ADR-031). Not shown at all unless the gateway says it can
/// understand images (`GET /api/v3/capabilities`): an attach button that can only produce a 503
/// is worse than none.
class AttachImageButton extends ConsumerWidget {
  final bool enabled;

  /// Seam for tests: returns the picked files. Defaults to the platform file picker.
  final Future<List<PlatformFile>> Function()? pickFiles;

  const AttachImageButton({super.key, required this.enabled, this.pickFiles});

  static Future<List<PlatformFile>> _platformPick() async {
    final picked = await FilePicker.platform.pickFiles(
      type: FileType.custom,
      allowedExtensions: kChatImageExtensions,
      allowMultiple: true,
      withData: true,
    );
    return picked?.files ?? const <PlatformFile>[];
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final capabilities = ref.watch(visionCapabilitiesProvider).value;
    if (capabilities == null || !capabilities.canAttach) return const SizedBox.shrink();
    final count = ref.watch(pendingChatImagesProvider).length;
    final full = count >= capabilities.maxImages;

    return IconButton(
      tooltip: full ? 'Image limit reached' : 'Attach image',
      icon: const Icon(Icons.image_outlined),
      onPressed: !enabled || full ? null : () => _attach(context, ref, capabilities),
    );
  }

  Future<void> _attach(BuildContext context, WidgetRef ref, VisionCapabilities limits) async {
    final files = await (pickFiles ?? _platformPick)();
    if (files.isEmpty || !context.mounted) return;
    final pending = ref.read(pendingChatImagesProvider.notifier);
    String? problem;
    for (final file in files) {
      final bytes = file.bytes;
      if (bytes == null) {
        problem = 'Could not read ${file.name}.';
        continue;
      }
      final rejection = pending.add(ChatImageAttachment(bytes: bytes, name: file.name), limits);
      if (rejection == AttachRejection.tooMany) {
        problem = 'You can attach up to ${limits.maxImages} images to one message.';
        break;
      }
      if (rejection == AttachRejection.tooLarge) {
        final mb = (limits.maxImageBytes / (1024 * 1024)).toStringAsFixed(0);
        problem = '${file.name} is larger than $mb MB.';
      }
    }
    if (problem != null && context.mounted) {
      AuraSnackbar.show(context, message: problem, kind: AuraSnackbarKind.error);
    }
  }
}

/// Thumbnails of the images that will go with the next message, each removable.
class PendingImagesStrip extends ConsumerWidget {
  final bool enabled;

  const PendingImagesStrip({super.key, required this.enabled});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final images = ref.watch(pendingChatImagesProvider);
    if (images.isEmpty) return const SizedBox.shrink();
    final palette = AuraPalette.of(context);

    return Padding(
      padding: const EdgeInsets.only(top: AuraSpacing.sm),
      child: Wrap(
        spacing: AuraSpacing.sm,
        runSpacing: AuraSpacing.sm,
        children: [
          for (var i = 0; i < images.length; i++)
            Semantics(
              label: 'Attached image ${i + 1} of ${images.length}: ${images[i].name}',
              image: true,
              child: Stack(
                clipBehavior: Clip.none,
                children: [
                  ClipRRect(
                    borderRadius: BorderRadius.circular(AuraRadius.sm),
                    child: Image.memory(
                      images[i].bytes,
                      width: 64,
                      height: 64,
                      fit: BoxFit.cover,
                      excludeFromSemantics: true,
                      errorBuilder: (_, _, _) => Container(
                        width: 64,
                        height: 64,
                        color: palette.surfaceVariant,
                        child: Icon(Icons.broken_image_outlined, color: palette.textSecondary),
                      ),
                    ),
                  ),
                  Positioned(
                    top: -10,
                    right: -10,
                    child: IconButton(
                      tooltip: 'Remove image ${i + 1}',
                      iconSize: 18,
                      visualDensity: VisualDensity.compact,
                      icon: Icon(Icons.cancel, color: palette.textSecondary),
                      onPressed: enabled
                          ? () => ref.read(pendingChatImagesProvider.notifier).removeAt(i)
                          : null,
                    ),
                  ),
                ],
              ),
            ),
        ],
      ),
    );
  }
}
