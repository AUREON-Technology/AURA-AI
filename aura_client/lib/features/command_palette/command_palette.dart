import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../core/database/database.dart';
import '../../core/database/database_provider.dart';
import '../../core/design_system/design_system.dart';

/// Global command palette (Doc/V2_BLUEPRINT.md §3) — reachable from any
/// screen via Ctrl/Cmd+K or the floating trigger in [ShellLayout], instead
/// of requiring the user to know which of 6+ tabs a feature lives in (UX
/// finding #3 in the blueprint's audit). Combines static navigation
/// destinations with a live search over local tasks/events so "find and
/// go to X" is one interaction instead of tab-hop-then-scroll.
class _Destination {
  final String label;
  final IconData icon;
  final String route;
  const _Destination(this.label, this.icon, this.route);
}

const _destinations = [
  _Destination('Today', Icons.today_outlined, '/home'),
  _Destination('Tasks', Icons.check_circle_outline, '/tasks'),
  _Destination('Calendar', Icons.calendar_month_outlined, '/calendar'),
  _Destination('Finance', Icons.monetization_on_outlined, '/finance'),
  _Destination('Health', Icons.favorite_outline, '/health'),
  _Destination('Copilot', Icons.auto_awesome_outlined, '/chat'),
  _Destination('Automations', Icons.bolt_outlined, '/automations'),
  _Destination('Knowledge Center', Icons.folder_shared_outlined, '/knowledge'),
  _Destination('System Analytics', Icons.bar_chart_outlined, '/analytics'),
  _Destination('AI Engine', Icons.cloud_outlined, '/models'),
  _Destination('Continuous Learning', Icons.psychology_outlined, '/learning'),
  _Destination('Profile', Icons.person_outline, '/profile'),
  _Destination('Settings', Icons.settings_outlined, '/settings'),
];

Future<void> showCommandPalette(BuildContext context, WidgetRef ref) {
  return showDialog(
    context: context,
    barrierColor: Colors.black54,
    builder: (context) => const _CommandPaletteDialog(),
  );
}

class _CommandPaletteDialog extends ConsumerStatefulWidget {
  const _CommandPaletteDialog();

  @override
  ConsumerState<_CommandPaletteDialog> createState() =>
      _CommandPaletteDialogState();
}

class _CommandPaletteDialogState extends ConsumerState<_CommandPaletteDialog> {
  final _controller = TextEditingController();
  String _query = '';
  List<Task> _matchingTasks = const [];
  List<CalendarEvent> _matchingEvents = const [];

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _search(String query) async {
    setState(() => _query = query);
    if (query.trim().length < 2) {
      setState(() {
        _matchingTasks = const [];
        _matchingEvents = const [];
      });
      return;
    }
    final db = ref.read(databaseProvider);
    final lower = query.trim().toLowerCase();
    // Local substring search over already-decrypted in-memory titles
    // (Drift's LIKE can't match against AES-GCM ciphertext at the SQL
    // layer, so filtering happens client-side after decryption).
    final tasks = await db.select(db.tasks).get();
    final events = await db.select(db.calendarEvents).get();
    if (!mounted) return;
    setState(() {
      _matchingTasks = tasks
          .where((t) => t.title.toLowerCase().contains(lower))
          .take(5)
          .toList();
      _matchingEvents = events
          .where((e) => e.title.toLowerCase().contains(lower))
          .take(5)
          .toList();
    });
  }

  List<_Destination> get _matchingDestinations {
    if (_query.trim().isEmpty) return _destinations;
    final lower = _query.trim().toLowerCase();
    return _destinations
        .where((d) => d.label.toLowerCase().contains(lower))
        .toList();
  }

  void _go(String route) {
    Navigator.of(context).pop();
    context.go(route);
  }

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final destinations = _matchingDestinations;

    final viewInsets = MediaQuery.viewInsetsOf(context);
    final screenHeight = MediaQuery.sizeOf(context).height;
    final topInset = screenHeight < 700 ? 24.0 : 100.0;
    final maxHeight = (screenHeight - topInset - viewInsets.bottom - 24).clamp(
      180.0,
      480.0,
    );

    return Dialog(
      alignment: Alignment.topCenter,
      insetPadding: EdgeInsets.only(
        top: topInset,
        left: 24,
        right: 24,
        bottom: 24 + viewInsets.bottom,
      ),
      backgroundColor: Colors.transparent,
      child: ConstrainedBox(
        constraints: BoxConstraints(maxWidth: 560, maxHeight: maxHeight),
        child: AuraCard(
          padding: EdgeInsets.zero,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Padding(
                padding: const EdgeInsets.symmetric(
                  horizontal: AuraSpacing.lg,
                  vertical: AuraSpacing.sm,
                ),
                child: Row(
                  children: [
                    Icon(Icons.search, color: palette.textSecondary, size: 20),
                    const SizedBox(width: AuraSpacing.md),
                    Expanded(
                      child: TextField(
                        controller: _controller,
                        autofocus: true,
                        onChanged: _search,
                        style: Theme.of(context).textTheme.bodyLarge,
                        decoration: const InputDecoration(
                          border: InputBorder.none,
                          hintText: 'Search AURA, or jump to a screen…',
                          isDense: true,
                        ),
                      ),
                    ),
                    Text(
                      'ESC',
                      style: TextStyle(
                        color: palette.textDisabled,
                        fontSize: 11,
                      ),
                    ),
                  ],
                ),
              ),
              const Divider(height: 1),
              Flexible(
                child: ListView(
                  shrinkWrap: true,
                  padding: const EdgeInsets.symmetric(vertical: AuraSpacing.sm),
                  children: [
                    if (_matchingTasks.isNotEmpty) ...[
                      _GroupLabel('Tasks'),
                      for (final t in _matchingTasks)
                        _ResultTile(
                          icon: Icons.circle_outlined,
                          label: t.title,
                          onTap: () => _go('/tasks'),
                        ),
                    ],
                    if (_matchingEvents.isNotEmpty) ...[
                      _GroupLabel('Calendar'),
                      for (final e in _matchingEvents)
                        _ResultTile(
                          icon: Icons.event_outlined,
                          label: e.title,
                          onTap: () => _go('/calendar'),
                        ),
                    ],
                    if (destinations.isNotEmpty) ...[
                      _GroupLabel('Go to'),
                      for (final d in destinations)
                        _ResultTile(
                          icon: d.icon,
                          label: d.label,
                          onTap: () => _go(d.route),
                        ),
                    ],
                    if (destinations.isEmpty &&
                        _matchingTasks.isEmpty &&
                        _matchingEvents.isEmpty)
                      const AuraEmptyState(
                        icon: Icons.search_off,
                        title: 'No matches',
                      ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _GroupLabel extends StatelessWidget {
  final String text;
  const _GroupLabel(this.text);

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    return Padding(
      padding: const EdgeInsets.fromLTRB(
        AuraSpacing.lg,
        AuraSpacing.md,
        AuraSpacing.lg,
        AuraSpacing.xs,
      ),
      child: Text(
        text.toUpperCase(),
        style: TextStyle(
          color: palette.textDisabled,
          fontSize: 11,
          fontWeight: FontWeight.w700,
          letterSpacing: 0.6,
        ),
      ),
    );
  }
}

class _ResultTile extends StatelessWidget {
  final IconData icon;
  final String label;
  final VoidCallback onTap;
  const _ResultTile({
    required this.icon,
    required this.label,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    return Semantics(
      button: true,
      label: label,
      child: InkWell(
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.symmetric(
            horizontal: AuraSpacing.lg,
            vertical: AuraSpacing.sm,
          ),
          child: Row(
            children: [
              Icon(icon, size: 18, color: palette.textSecondary),
              const SizedBox(width: AuraSpacing.md),
              Expanded(
                child: Text(
                  label,
                  style: Theme.of(
                    context,
                  ).textTheme.bodyMedium?.copyWith(color: palette.textPrimary),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
