import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';

import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/core/network/aura_failure.dart';
import 'package:aura_client/core/network/aura_failure_view.dart';
import 'package:aura_client/features/chat/data/feedback_repository.dart';

class FeedbackPage extends ConsumerWidget {
  const FeedbackPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final logsAsync = ref.watch(localFeedbackStreamProvider);
    final palette = AuraPalette.of(context);

    return Scaffold(
      backgroundColor: palette.background,
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        elevation: 0,
        title: Text('Feedback', style: Theme.of(context).textTheme.headlineMedium),
      ),
      body: logsAsync.when(
        data: (logs) {
          if (logs.isEmpty) {
            return const AuraEmptyState(
              icon: Icons.rate_review_outlined,
              title: 'No corrections yet',
              message: 'Rate a Copilot answer to help AURA learn what you prefer.',
            );
          }
          return ListView.separated(
            padding: const EdgeInsets.all(AuraSpacing.lg),
            itemCount: logs.length,
            separatorBuilder: (_, _) => const SizedBox(height: AuraSpacing.sm),
            itemBuilder: (context, index) {
              final log = logs[index];
              return AuraCard(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'Rated ${log.rating}/5 · ${DateFormat.yMMMd().format(log.createdAt)}',
                      style: Theme.of(context).textTheme.titleSmall,
                    ),
                    if (log.correction != null && log.correction!.isNotEmpty) ...[
                      const SizedBox(height: AuraSpacing.sm),
                      Text(log.correction!, style: Theme.of(context).textTheme.bodyMedium),
                    ],
                    if (log.response.isNotEmpty) ...[
                      const SizedBox(height: AuraSpacing.sm),
                      Text(
                        log.response,
                        maxLines: 3,
                        overflow: TextOverflow.ellipsis,
                        style: Theme.of(context).textTheme.bodySmall,
                      ),
                    ],
                  ],
                ),
              );
            },
          );
        },
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => AuraFailureView(
          failure: AuraFailure.from(e),
          onRetry: () => ref.invalidate(localFeedbackStreamProvider),
        ),
      ),
    );
  }
}
