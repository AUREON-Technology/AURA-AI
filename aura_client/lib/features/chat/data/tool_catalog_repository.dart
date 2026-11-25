import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'package:aura_client/core/network/aura_api_client.dart';
import 'package:aura_client/core/workspaces/active_workspace.dart';

/// One tool from `GET /api/v3/tools` (`aura_core/tools/` specs, filtered to what *this caller's*
/// capabilities may use). The client used to hold its own hand-kept icon table for nine tools and
/// showed everything else as a raw `snake_case` name; the backend already describes every tool it
/// will run, so the chips and the confirmation card read that instead.
class ToolInfo {
  final String name;
  final String description;

  /// utility | knowledge | memory | productivity | communication | development | data | media |
  /// system. An unknown value is kept as sent and shown with the generic icon.
  final String category;

  /// The tool changes something outside the conversation (writes a file, runs a command, opens a
  /// pull request).
  final bool sideEffecting;

  /// The server will hold a call to this tool for the user's approval before running it.
  final bool requiresConfirmation;

  /// `{argument: description}` taken from the tool's JSON schema, for labelling a call's arguments.
  final Map<String, String> parameterDescriptions;

  const ToolInfo({
    required this.name,
    this.description = '',
    this.category = '',
    this.sideEffecting = false,
    this.requiresConfirmation = false,
    this.parameterDescriptions = const {},
  });

  factory ToolInfo.fromJson(Map<String, dynamic> json) {
    final params = <String, String>{};
    final schema = json['parameters'];
    final properties = schema is Map ? schema['properties'] : null;
    if (properties is Map) {
      properties.forEach((key, value) {
        final text = value is Map ? value['description'] : null;
        if (text is String && text.trim().isNotEmpty) params[key.toString()] = text.trim();
      });
    }
    return ToolInfo(
      name: json['name']?.toString() ?? '',
      description: json['description']?.toString() ?? '',
      category: json['category']?.toString() ?? '',
      sideEffecting: json['side_effecting'] == true,
      requiresConfirmation: json['requires_confirmation'] == true,
      parameterDescriptions: params,
    );
  }

  /// The description's first sentence, bounded: a tool description is written for the model and
  /// can run to a paragraph, which is too long for a tooltip or a card subtitle.
  String get summary {
    final text = description.trim().replaceAll(RegExp(r'\s+'), ' ');
    if (text.isEmpty) return '';
    final end = text.indexOf(RegExp(r'[.!?](\s|$)'));
    final sentence = end == -1 ? text : text.substring(0, end + 1);
    return sentence.length <= 140 ? sentence : '${sentence.substring(0, 139).trimRight()}…';
  }

  /// `write_file` -> `Write File`: what a chip says when nothing better is known.
  String get label => prettyToolName(name);
}

String prettyToolName(String name) => name
    .split('_')
    .where((w) => w.isNotEmpty)
    .map((w) => '${w[0].toUpperCase()}${w.substring(1)}')
    .join(' ');

/// The category's icon. A tool's own curated icon (where the client has one) wins over this.
IconData iconForToolCategory(String category) => switch (category) {
      'utility' => Icons.handyman_outlined,
      'knowledge' => Icons.search,
      'memory' => Icons.psychology_outlined,
      'productivity' => Icons.checklist_outlined,
      'communication' => Icons.forum_outlined,
      'development' => Icons.code,
      'data' => Icons.table_chart_outlined,
      'media' => Icons.perm_media_outlined,
      'system' => Icons.settings_outlined,
      _ => Icons.build_outlined,
    };

/// Every tool the caller may use, by name. Empty when the list could not be fetched: a chip or a
/// card then renders exactly as it did before the catalog existed, never blank and never an error.
class ToolCatalog {
  final Map<String, ToolInfo> _byName;

  const ToolCatalog._(this._byName);

  static const ToolCatalog empty = ToolCatalog._({});

  factory ToolCatalog.of(Iterable<ToolInfo> tools) =>
      ToolCatalog._({for (final t in tools) if (t.name.isNotEmpty) t.name: t});

  factory ToolCatalog.fromJson(Map<String, dynamic> json) {
    final rows = json['tools'];
    return ToolCatalog.of(
      (rows is List ? rows : const [])
          .whereType<Map>()
          .map((r) => ToolInfo.fromJson(r.map((k, v) => MapEntry(k.toString(), v)))),
    );
  }

  ToolInfo? operator [](String name) => _byName[name];

  bool get isEmpty => _byName.isEmpty;
  int get length => _byName.length;
}

/// `GET /api/v3/tools`, fetched again when the active workspace changes (the list is filtered by
/// the caller's capabilities, and the Engineer's tools depend on the workspace's plan).
///
/// A failure is an empty catalog, not an error: this only decorates chips and cards, and a chip
/// must still render when the endpoint is unreachable.
final toolCatalogProvider = FutureProvider<ToolCatalog>((ref) async {
  ref.watch(activeWorkspaceProvider.select((w) => w?.id));
  try {
    final decoded = await ref.read(auraApiClientProvider).json(
          method: 'GET',
          path: '/api/v3/tools',
        );
    return ToolCatalog.fromJson(decoded);
  } on Object {
    return ToolCatalog.empty;
  }
});
