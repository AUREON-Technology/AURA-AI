import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/ai/vision/chat_images.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/l10n/l10n.dart';

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
      tooltip: full ? context.l10n.chatImageLimitReached : context.l10n.chatAttachImage,
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
      if (rejection == AttachRejection.totalTooLarge) {
        final mb = (limits.maxRequestImageBytes / (1024 * 1024)).toStringAsFixed(0);
        problem = '${file.name} was not added: one message can carry at most $mb MB of images.';
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
              label: context.l10n.chatAttachedImageLabel(i + 1, images.length, images[i].name),
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
                  PositionedDirectional(
                    top: -10,
                    end: -10,
                    child: IconButton(
                      tooltip: context.l10n.chatRemoveImage(i + 1),
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

/// What a sent user message shows for its images (ADR-031 P5-4): thumbnails while this session
/// still holds them, each opening a full-size preview, otherwise a chip saying how many were
/// attached. The images themselves are never stored, so a reloaded conversation has only the
/// count.
class SentImagesView extends ConsumerWidget {
  final String messageId;
  final int imageCount;
  final Color foreground;

  const SentImagesView({
    super.key,
    required this.messageId,
    required this.imageCount,
    required this.foreground,
  });

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final images = ref.watch(sentChatImagesProvider)[messageId] ?? const <ChatImageAttachment>[];
    if (images.isEmpty) {
      if (imageCount == 0) return const SizedBox.shrink();
      final label = imageCount == 1 ? '1 image attached' : '$imageCount images attached';
      return Semantics(
        label: label,
        excludeSemantics: true,
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.image_outlined, size: 16, color: foreground.withValues(alpha: 0.85)),
            const SizedBox(width: AuraSpacing.xs),
            Text(label, style: TextStyle(fontSize: 12, color: foreground.withValues(alpha: 0.85))),
          ],
        ),
      );
    }

    return Wrap(
      spacing: AuraSpacing.sm,
      runSpacing: AuraSpacing.sm,
      children: [
        for (var i = 0; i < images.length; i++)
          Semantics(
            button: true,
            label: context.l10n.chatSentImageLabel(i + 1, images.length, images[i].name),
            excludeSemantics: true,
            child: InkWell(
              borderRadius: BorderRadius.circular(AuraRadius.sm),
              onTap: () => _preview(context, images[i]),
              child: ClipRRect(
                borderRadius: BorderRadius.circular(AuraRadius.sm),
                child: Image.memory(
                  images[i].bytes,
                  width: 96,
                  height: 96,
                  fit: BoxFit.cover,
                  gaplessPlayback: true,
                  errorBuilder: (_, _, _) => SizedBox(
                    width: 96,
                    height: 96,
                    child: Icon(Icons.broken_image_outlined, color: foreground),
                  ),
                ),
              ),
            ),
          ),
      ],
    );
  }

  static Future<void> _preview(BuildContext context, ChatImageAttachment image) {
    return showDialog<void>(
      context: context,
      builder: (dialogContext) => Dialog(
        insetPadding: const EdgeInsets.all(AuraSpacing.lg),
        clipBehavior: Clip.antiAlias,
        // The close button sits in its own row rather than over the image: a dialog sized to a
        // small image would otherwise clip it out of reach.
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.end,
          children: [
            IconButton(
              tooltip: dialogContext.l10n.chatClosePreview,
              icon: const Icon(Icons.close),
              onPressed: () => Navigator.of(dialogContext).pop(),
            ),
            Flexible(
              child: ConstrainedBox(
                constraints: const BoxConstraints(minWidth: 240, minHeight: 160),
                child: InteractiveViewer(
                  maxScale: 5,
                  child: Image.memory(image.bytes, fit: BoxFit.contain, semanticLabel: image.name),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
