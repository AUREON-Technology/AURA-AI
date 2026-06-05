import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import '../../../core/config/config.dart';
import '../../../core/theme/theme.dart';
import '../../auth/presentation/auth_provider.dart';

final learningStatsProvider = FutureProvider.autoDispose<Map<String, dynamic>>((ref) async {
  final token = await ref.read(authProvider.notifier).getIdToken();
  final headers = <String, String>{};
  if (token != null) {
    headers['Authorization'] = 'Bearer $token';
  }
  
  final url = Uri.parse("${AppConfig.backendUrl}/api/learning/evaluate");
  final response = await http.get(url, headers: headers).timeout(const Duration(seconds: 10));
  
  if (response.statusCode == 200) {
    return jsonDecode(response.body) as Map<String, dynamic>;
  } else {
    throw Exception("Server returned code ${response.statusCode}");
  }
});

class LearningDashboard extends ConsumerStatefulWidget {
  const LearningDashboard({super.key});

  @override
  ConsumerState<LearningDashboard> createState() => _LearningDashboardState();
}

class _LearningDashboardState extends ConsumerState<LearningDashboard> {
  bool _isBuilding = false;
  String? _buildMessage;
  String _selectedFormat = "alpaca";

  Future<void> _buildDataset() async {
    setState(() {
      _isBuilding = true;
      _buildMessage = null;
    });

    try {
      final token = await ref.read(authProvider.notifier).getIdToken();
      final headers = <String, String>{};
      if (token != null) {
        headers['Authorization'] = 'Bearer $token';
      }

      final url = Uri.parse("${AppConfig.backendUrl}/api/learning/build_dataset");
      final response = await http.post(url, headers: headers).timeout(const Duration(seconds: 15));

      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        setState(() {
          _buildMessage = "Dataset built successfully! Saved as ${data['file_saved']} (${data['total_records']} records).";
        });
        ref.refresh(learningStatsProvider);
      } else {
        setState(() {
          _buildMessage = "Build failed: Server returned ${response.statusCode}";
        });
      }
    } catch (e) {
      setState(() {
        _buildMessage = "Error building dataset: $e";
      });
    } finally {
      setState(() {
        _isBuilding = false;
      });
    }
  }

  Future<void> _exportDataset() async {
    try {
      final token = await ref.read(authProvider.notifier).getIdToken();
      final headers = <String, String>{};
      if (token != null) {
        headers['Authorization'] = 'Bearer $token';
      }

      final url = Uri.parse("${AppConfig.backendUrl}/api/learning/export?format=$_selectedFormat");
      final response = await http.get(url, headers: headers).timeout(const Duration(seconds: 15));

      if (response.statusCode == 200) {
        final datasetText = response.body;
        if (mounted) {
          showDialog(
            context: context,
            builder: (context) {
              return AlertDialog(
                backgroundColor: AuraTheme.background,
                title: Text(
                  "Exported Dataset ($_selectedFormat)",
                  style: const TextStyle(color: Colors.white, fontWeight: FontWeight.bold),
                ),
                content: SingleChildScrollView(
                  child: Container(
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: Colors.black26,
                      borderRadius: BorderRadius.circular(8),
                      border: Border.all(color: Colors.white10),
                    ),
                    child: SelectableText(
                      datasetText,
                      style: const TextStyle(
                        fontFamily: 'monospace',
                        fontSize: 11,
                        color: AuraTheme.textSecondary,
                      ),
                    ),
                  ),
                ),
                actions: [
                  TextButton(
                    onPressed: () => Navigator.pop(context),
                    child: const Text("Close", style: TextStyle(color: AuraTheme.accentIndigo)),
                  ),
                ],
              );
            },
          );
        }
      } else {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text("Export failed: Status ${response.statusCode}")),
        );
      }
    } catch (e) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text("Error exporting dataset: $e")),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final statsAsync = ref.watch(learningStatsProvider);

    return Scaffold(
      backgroundColor: AuraTheme.background,
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        elevation: 0,
        title: const Text(
          "Continuous Learning & LoRA",
          style: TextStyle(fontWeight: FontWeight.bold, color: Colors.white),
        ),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh, color: Colors.white),
            onPressed: () => ref.refresh(learningStatsProvider),
          ),
        ],
      ),
      body: statsAsync.when(
        data: (stats) {
          final totalRecords = stats["total_records"] ?? 0;
          final avgRating = stats["average_rating"] ?? 0.0;
          final correctedPct = stats["corrected_percentage"] ?? 0.0;
          final models = stats["model_distributions"] as Map<String, dynamic>? ?? {};

          return Padding(
            padding: const EdgeInsets.all(24.0),
            child: SingleChildScrollView(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  _buildIntroCard(),
                  const SizedBox(height: 24),
                  Text(
                    "Telemetry & Learning Stats",
                    style: Theme.of(context).textTheme.titleMedium?.copyWith(
                          color: Colors.white,
                          fontWeight: FontWeight.bold,
                        ),
                  ),
                  const SizedBox(height: 14),
                  _buildStatsRow(totalRecords, avgRating, correctedPct),
                  const SizedBox(height: 24),
                  if (models.isNotEmpty) ...[
                    _buildModelsDistributionCard(models),
                    const SizedBox(height: 24),
                  ],
                  _buildActionCard(),
                  if (_buildMessage != null) ...[
                    const SizedBox(height: 16),
                    Text(
                      _buildMessage!,
                      textAlign: TextAlign.center,
                      style: TextStyle(
                        color: _buildMessage!.contains("Error") || _buildMessage!.contains("failed")
                            ? Colors.redAccent
                            : AuraTheme.accentTeal,
                        fontSize: 13,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                  ],
                ],
              ),
            ),
          );
        },
        loading: () => const Center(child: CircularProgressIndicator(color: AuraTheme.accentIndigo)),
        error: (err, _) => Center(
          child: Text(
            "Failed to load learning statistics: $err",
            style: const TextStyle(color: Colors.redAccent),
          ),
        ),
      ),
    );
  }

  Widget _buildIntroCard() {
    return Card(
      color: AuraTheme.cardBg,
      child: const Padding(
        padding: EdgeInsets.all(20.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(Icons.psychology, color: AuraTheme.accentIndigo, size: 28),
                SizedBox(width: 12),
                Text(
                  "Continuous Learning Engine",
                  style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold, color: Colors.white),
                ),
              ],
            ),
            SizedBox(height: 10),
            Text(
              "AURA AI leverages feedback loops. When you correct agent outputs, those prompts, completions, and ratings are logged and stored. You can compile these feedback records into LoRA-ready training datasets to specialize future models.",
              style: TextStyle(fontSize: 13, color: AuraTheme.textSecondary, height: 1.4),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildStatsRow(int totalRecords, double avgRating, double correctedPct) {
    return Row(
      children: [
        Expanded(
          child: _buildStatItem("Total Samples", totalRecords.toString(), Icons.analytics),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: _buildStatItem("Avg Rating", "${avgRating.toStringAsFixed(1)} / 5.0", Icons.star_border),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: _buildStatItem("Corrections", "${correctedPct.toStringAsFixed(0)}%", Icons.edit_note),
        ),
      ],
    );
  }

  Widget _buildStatItem(String title, String value, IconData icon) {
    return Card(
      color: AuraTheme.cardBg,
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 16, horizontal: 12),
        child: Column(
          children: [
            Icon(icon, color: AuraTheme.accentAmber, size: 22),
            const SizedBox(height: 8),
            Text(
              value,
              style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold, color: Colors.white),
            ),
            const SizedBox(height: 4),
            Text(
              title,
              style: const TextStyle(fontSize: 10, color: AuraTheme.textSecondary),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildModelsDistributionCard(Map<String, dynamic> models) {
    return Card(
      color: AuraTheme.cardBg,
      child: Padding(
        padding: const EdgeInsets.all(20.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              "Inference Model Logs",
              style: TextStyle(fontSize: 14, fontWeight: FontWeight.bold, color: Colors.white),
            ),
            const SizedBox(height: 12),
            ...models.entries.map((entry) {
              return Padding(
                padding: const EdgeInsets.symmetric(vertical: 4.0),
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Text(
                      entry.key,
                      style: const TextStyle(color: AuraTheme.textSecondary, fontSize: 13),
                    ),
                    Text(
                      "${entry.value} logs",
                      style: const TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 13),
                    ),
                  ],
                ),
              );
            }).toList(),
          ],
        ),
      ),
    );
  }

  Widget _buildActionCard() {
    return Card(
      color: AuraTheme.cardBg,
      child: Padding(
        padding: const EdgeInsets.all(20.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              "LoRA Dataset Pipelines",
              style: TextStyle(fontSize: 14, fontWeight: FontWeight.bold, color: Colors.white),
            ),
            const SizedBox(height: 10),
            const Text(
              "Process local learning logs, validate fields, and format dataset rows ready for training.",
              style: TextStyle(fontSize: 12, color: AuraTheme.textSecondary),
            ),
            const SizedBox(height: 20),
            Row(
              children: [
                Expanded(
                  child: ElevatedButton.icon(
                    onPressed: _isBuilding ? null : _buildDataset,
                    icon: _isBuilding
                        ? const SizedBox(
                            width: 16,
                            height: 16,
                            child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                          )
                        : const Icon(Icons.build, size: 16),
                    label: const Text("Compile Dataset"),
                    style: ElevatedButton.styleFrom(
                      backgroundColor: AuraTheme.accentIndigo,
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 16),
            const Divider(color: Colors.white10),
            const SizedBox(height: 12),
            const Text(
              "Dataset Export Format",
              style: TextStyle(fontSize: 13, color: Colors.white70, fontWeight: FontWeight.bold),
            ),
            const SizedBox(height: 10),
            Row(
              children: [
                Expanded(
                  child: DropdownButtonFormField<String>(
                    dropdownColor: AuraTheme.background,
                    value: _selectedFormat,
                    items: const [
                      DropdownMenuItem(value: "alpaca", child: Text("Alpaca Formatted")),
                      DropdownMenuItem(value: "chatml", child: Text("ChatML Formatted")),
                      DropdownMenuItem(value: "openai", child: Text("OpenAI Conversations")),
                    ],
                    onChanged: (val) {
                      if (val != null) {
                        setState(() {
                          _selectedFormat = val;
                        });
                      }
                    },
                    decoration: InputDecoration(
                      contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                      border: OutlineInputBorder(borderRadius: BorderRadius.circular(8)),
                    ),
                  ),
                ),
                const SizedBox(width: 12),
                ElevatedButton.icon(
                  onPressed: _exportDataset,
                  icon: const Icon(Icons.ios_share, size: 16),
                  label: const Text("Export"),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: AuraTheme.accentTeal,
                    padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
                  ),
                )
              ],
            )
          ],
        ),
      ),
    );
  }
}
