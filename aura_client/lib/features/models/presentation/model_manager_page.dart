import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:aura_client/core/ai/ai_router/aura_ai_router.dart';
import 'package:aura_client/core/theme/theme.dart';

/// Reports which engine is serving requests. Read-only, because there is nothing here to manage.
///
/// This page used to download GGUF model weights from HuggingFace onto the device, activate one of
/// them, delete them, and show the reachability of an Ollama daemon whose address the user typed in.
/// All of that is gone:
///
///  * Inference runs on the server. A phone cannot run a useful model, and the ones it can run are
///    far worse than the one the server already has.
///  * The downloads were unpinned and unverified -- multi-gigabyte executable artefacts fetched over
///    the network with no digest check, onto user devices.
///  * The client is not supposed to know the model server's address at all, which is what the
///    Ollama host field exposed.
///
/// The route is kept rather than deleted so navigation is unchanged and a bookmark or a deep link
/// still resolves. What it shows is now the truth: models are managed centrally, and here is
/// whether we can reach them.
class ModelManagerPage extends ConsumerWidget {
  const ModelManagerPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final status = ref.watch(aiRouterStatusProvider);

    return Scaffold(
      backgroundColor: AuraTheme.background,
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        elevation: 0,
        title: const Text(
          "AI Engine",
          style: TextStyle(fontWeight: FontWeight.bold, color: Colors.white),
        ),
      ),
      body: Padding(
        padding: const EdgeInsets.all(24.0),
        child: SingleChildScrollView(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              _StatusCard(status: status),
              const SizedBox(height: 24),
              const _ExplanationCard(),
            ],
          ),
        ),
      ),
    );
  }
}

class _StatusCard extends StatelessWidget {
  final AIRouterStatus status;

  const _StatusCard({required this.status});

  @override
  Widget build(BuildContext context) {
    final reachable = !status.isOffline;
    return Card(
      color: AuraTheme.cardBg,
      child: Padding(
        padding: const EdgeInsets.all(20.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(
                  reachable ? Icons.cloud_done_outlined : Icons.cloud_off_outlined,
                  color: reachable ? Colors.greenAccent : Colors.orangeAccent,
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Text(
                    status.status,
                    style: const TextStyle(
                      color: Colors.white,
                      fontWeight: FontWeight.bold,
                      fontSize: 16,
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 12),
            Text(
              // Latency of the last turn, not a live probe: showing a number the app is not
              // currently measuring would be inventing one.
              status.latencyMs > 0
                  ? "Last response in ${status.latencyMs.toStringAsFixed(0)} ms"
                  : "No requests yet this session",
              style: const TextStyle(color: Colors.white70, fontSize: 13),
            ),
          ],
        ),
      ),
    );
  }
}

class _ExplanationCard extends StatelessWidget {
  const _ExplanationCard();

  @override
  Widget build(BuildContext context) {
    return Card(
      color: AuraTheme.cardBg,
      child: Padding(
        padding: const EdgeInsets.all(20.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: const [
            Text(
              "Models run on AURA's servers",
              style: TextStyle(
                color: Colors.white,
                fontWeight: FontWeight.bold,
                fontSize: 15,
              ),
            ),
            SizedBox(height: 10),
            Text(
              "Your requests are processed on AURA's own infrastructure — never sent to a "
              "third-party AI service, and never processed on this device.\n\n"
              "Model selection, updates and rollbacks are handled centrally, so there is nothing "
              "to download or configure here.",
              style: TextStyle(color: Colors.white70, fontSize: 13, height: 1.5),
            ),
          ],
        ),
      ),
    );
  }
}
