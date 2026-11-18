import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../../features/analytics/presentation/analytics_page.dart';
import '../../features/auth/presentation/auth_provider.dart';
import '../../features/auth/presentation/linked_accounts_page.dart';
import '../../features/auth/presentation/login_page.dart';
import '../../features/auth/presentation/profile_page.dart';
import '../../features/automations/presentation/automations_page.dart';
import '../../features/chat/presentation/chat_page.dart';
import '../../features/chat/presentation/chat_route_extra.dart';
import '../../features/chat/presentation/widgets/capture_sheet.dart';
import '../../features/command_palette/command_palette.dart';
import '../sync/sync_alert_banner.dart';
import '../../features/engineering/presentation/engineer_history_page.dart';
import '../../features/engineering/presentation/github_connection_page.dart';
import '../../features/engineering/presentation/host_daemon_page.dart';
import '../../features/engineering/presentation/host_file_browser_page.dart';
import '../../features/feedback/presentation/feedback_page.dart';
import '../../features/home/presentation/home_page.dart';
import '../../features/knowledge/presentation/knowledge_center_page.dart';
import '../../features/life_hub/presentation/life_hub_page.dart';
import '../../features/memory/presentation/memory_page.dart';
import '../../features/models/presentation/model_manager_page.dart';
import '../../features/onboarding/presentation/onboarding_gate.dart';
import '../../features/onboarding/presentation/onboarding_page.dart';
import '../../features/projects/presentation/projects_page.dart';
import '../../features/goals/presentation/goals_page.dart';
import '../../features/settings/presentation/backend_environment_page.dart';
import '../../features/settings/presentation/appearance_page.dart';
import '../../features/settings/presentation/desktop_devices_page.dart';
import '../../features/settings/presentation/settings_page.dart';
import '../../features/workspaces/presentation/invite_accept_page.dart';
import '../../features/workspaces/presentation/pending_invite.dart';
import '../../features/workspaces/presentation/plan_page.dart';
import '../../features/workspaces/presentation/shared_chats_page.dart';
import '../../features/workspaces/presentation/team_settings_page.dart';
import '../../l10n/generated/app_localizations.dart';
import '../../features/voice_live/presentation/live_voice_page.dart';
import '../design_system/design_system.dart';
import '../platform/aura_platform.dart';
import '../../desktop/shell/desktop_shell.dart';
import '../../desktop/workspace/workspace_welcome_page.dart';
import '../../desktop/workspace/workspace_manager_page.dart';

class ShellLayout extends ConsumerWidget {
  final Widget child;
  const ShellLayout({super.key, required this.child});

  int _calculateSelectedIndex(BuildContext context) {
    final path = GoRouterState.of(context).uri.path;
    if (path.startsWith('/chat')) return 1;
    if (path.startsWith('/life')) return 2;
    if (path.startsWith('/you') ||
        path.startsWith('/profile') ||
        path.startsWith('/knowledge') ||
        path.startsWith('/memory') ||
        path.startsWith('/feedback') ||
        path.startsWith('/models') ||
        path.startsWith('/analytics') ||
        path.startsWith('/automations') ||
        path.startsWith('/appearance') ||
        path.startsWith('/server-environment') ||
        path.startsWith('/engineer-history') ||
        path.startsWith('/github-connect') ||
        path.startsWith('/engineer-daemon') ||
        path.startsWith('/settings')) {
      return 3;
    }
    return 0;
  }

  void _onDestinationSelected(BuildContext context, int index) {
    switch (index) {
      case 0:
        context.go('/today');
      case 1:
        context.go('/chat');
      case 2:
        context.go('/life');
      case 3:
        context.go('/you');
    }
  }

  bool _showCaptureFab(BuildContext context) {
    final path = GoRouterState.of(context).uri.path;
    return !path.startsWith('/chat') && !path.startsWith('/life');
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final selectedIndex = _calculateSelectedIndex(context);
    final width = MediaQuery.of(context).size.width;
    final isMobile = width < 768;
    final isWide = width >= 1100;
    final palette = AuraPalette.of(context);
    final l10n = AppLocalizations.of(context);

    final shortcuts = <ShortcutActivator, VoidCallback>{
      const SingleActivator(LogicalKeyboardKey.keyK, control: true): () =>
          showCommandPalette(context, ref),
      const SingleActivator(LogicalKeyboardKey.keyK, meta: true): () =>
          showCommandPalette(context, ref),
    };

    final fab = _showCaptureFab(context)
        ? FloatingActionButton(
            heroTag: 'capture_fab',
            tooltip: 'Ask Copilot',
            onPressed: () => showCaptureSheet(context),
            child: const Icon(Icons.auto_awesome),
          )
        : null;

    if (isMobile) {
      return CallbackShortcuts(
        bindings: shortcuts,
        child: Focus(
          autofocus: true,
          child: Scaffold(
            floatingActionButton: fab,
            // The banner sits above the routed screen rather than inside any
            // one of them, so an unsaved change is visible wherever the user
            // happens to be. It renders nothing when there is nothing to say.
            body: SafeArea(
              child: Column(
                children: [
                  const SyncAlertBanner(),
                  Expanded(child: child),
                ],
              ),
            ),
            bottomNavigationBar: NavigationBar(
              selectedIndex: selectedIndex,
              onDestinationSelected: (index) =>
                  _onDestinationSelected(context, index),
              destinations: [
                NavigationDestination(
                  icon: const Icon(Icons.today_outlined),
                  selectedIcon: const Icon(Icons.today),
                  label: l10n?.navToday ?? 'Today',
                ),
                NavigationDestination(
                  icon: const Icon(Icons.auto_awesome_outlined),
                  selectedIcon: const Icon(Icons.auto_awesome),
                  label: l10n?.navCopilot ?? 'Copilot',
                ),
                NavigationDestination(
                  icon: const Icon(Icons.view_week_outlined),
                  selectedIcon: const Icon(Icons.view_week),
                  label: l10n?.navLifeHub ?? 'Life Hub',
                ),
                NavigationDestination(
                  icon: const Icon(Icons.person_outline),
                  selectedIcon: const Icon(Icons.person),
                  label: l10n?.navYou ?? 'You',
                ),
              ],
            ),
          ),
        ),
      );
    }

    return CallbackShortcuts(
      bindings: shortcuts,
      child: Focus(
        autofocus: true,
        child: Scaffold(
          floatingActionButton: fab,
          body: Row(
            children: [
              NavigationRail(
                selectedIndex: selectedIndex,
                onDestinationSelected: (index) =>
                    _onDestinationSelected(context, index),
                // Wide windows get a labelled sidebar plus direct links into
                // the pages that otherwise sit two taps deep under You; a
                // narrower window keeps the compact rail.
                extended: isWide,
                minExtendedWidth: 232,
                labelType: isWide
                    ? NavigationRailLabelType.none
                    : NavigationRailLabelType.all,
                leading: _RailBrand(extended: isWide),
                trailing: isWide ? const _RailShortcuts() : null,
                // A short window must not clip the shortcuts.
                scrollable: true,
                destinations: [
                  NavigationRailDestination(
                    icon: const Icon(Icons.today_outlined),
                    selectedIcon: const Icon(Icons.today),
                    label: Text(l10n?.navToday ?? 'Today'),
                  ),
                  NavigationRailDestination(
                    icon: const Icon(Icons.auto_awesome_outlined),
                    selectedIcon: const Icon(Icons.auto_awesome),
                    label: Text(l10n?.navCopilot ?? 'Copilot'),
                  ),
                  NavigationRailDestination(
                    icon: const Icon(Icons.view_week_outlined),
                    selectedIcon: const Icon(Icons.view_week),
                    label: Text(l10n?.navLifeHub ?? 'Life Hub'),
                  ),
                  NavigationRailDestination(
                    icon: const Icon(Icons.person_outline),
                    selectedIcon: const Icon(Icons.person),
                    label: Text(l10n?.navYou ?? 'You'),
                  ),
                ],
              ),
              VerticalDivider(thickness: 1, width: 1, color: palette.border),
              Expanded(
                child: Column(
                  children: [
                    const SyncAlertBanner(),
                    Expanded(child: child),
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

/// The brand mark at the top of the wide-layout rail: the app icon on a
/// brand-gradient tile, with the wordmark beside it once the rail is extended.
class _RailBrand extends StatelessWidget {
  final bool extended;
  const _RailBrand({required this.extended});

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final mark = Container(
      width: 40,
      height: 40,
      padding: const EdgeInsets.all(6),
      decoration: BoxDecoration(
        gradient: palette.brandGradient,
        borderRadius: BorderRadius.circular(AuraRadius.md),
        boxShadow: [
          BoxShadow(
            color: palette.brand.withValues(alpha: 0.35),
            blurRadius: 16,
            offset: const Offset(0, 6),
          ),
        ],
      ),
      child: Image.asset('assets/app_icon.png', fit: BoxFit.contain),
    );
    final wordmark = Text(
      'AURA',
      style: Theme.of(context).textTheme.titleMedium?.copyWith(
        fontWeight: FontWeight.w800,
        letterSpacing: 2.0,
      ),
    );
    return Padding(
      padding: const EdgeInsets.fromLTRB(
        AuraSpacing.lg,
        AuraSpacing.xl,
        AuraSpacing.lg,
        AuraSpacing.xl,
      ),
      child: extended
          ? SizedBox(
              width: 200,
              child: Row(
                children: [
                  mark,
                  const SizedBox(width: AuraSpacing.md),
                  wordmark,
                ],
              ),
            )
          : Column(
              children: [
                mark,
                const SizedBox(height: AuraSpacing.sm),
                Text(
                  'AURA',
                  style: Theme.of(context).textTheme.labelSmall?.copyWith(
                    fontWeight: FontWeight.w800,
                    letterSpacing: 1.0,
                  ),
                ),
              ],
            ),
    );
  }
}

/// Direct links, under the extended rail, to the most-used pages that live
/// under You. On a wide screen there is room for them, and they save the trip
/// through the You list for the pages people open every day.
class _RailShortcuts extends StatelessWidget {
  const _RailShortcuts();

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final l10n = AppLocalizations.of(context);
    final path = GoRouterState.of(context).uri.path;
    final links = <(IconData, String, String)>[
      (
        Icons.folder_shared_outlined,
        l10n?.knowledgeTitle ?? 'Knowledge',
        '/knowledge',
      ),
      (Icons.psychology_outlined, l10n?.memoryTitle ?? 'Memory', '/memory'),
      (
        Icons.bolt_outlined,
        l10n?.settingsAutomationsTitle ?? 'Automations',
        '/automations',
      ),
      (
        Icons.code_outlined,
        l10n?.settingsEngineerTitle ?? 'Engineer',
        '/engineer-history',
      ),
    ];
    return SizedBox(
      width: 232,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(
          AuraSpacing.md,
          AuraSpacing.xl,
          AuraSpacing.md,
          0,
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Padding(
              padding: const EdgeInsetsDirectional.only(
                start: AuraSpacing.md,
                bottom: AuraSpacing.sm,
              ),
              child: Text(
                (l10n?.settingsSectionYourAura ?? 'Your AURA').toUpperCase(),
                style: TextStyle(
                  color: palette.textDisabled,
                  fontSize: 11,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 0.8,
                ),
              ),
            ),
            for (final link in links)
              _RailShortcut(
                icon: link.$1,
                label: link.$2,
                selected: path.startsWith(link.$3),
                onTap: () => context.go(link.$3),
              ),
          ],
        ),
      ),
    );
  }
}

class _RailShortcut extends StatelessWidget {
  final IconData icon;
  final String label;
  final bool selected;
  final VoidCallback onTap;

  const _RailShortcut({
    required this.icon,
    required this.label,
    required this.selected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final color = selected ? palette.textPrimary : palette.textSecondary;
    return Semantics(
      button: true,
      selected: selected,
      child: Material(
        color: selected
            ? palette.brand.withValues(alpha: 0.14)
            : Colors.transparent,
        borderRadius: BorderRadius.circular(AuraRadius.sm),
        child: InkWell(
          borderRadius: BorderRadius.circular(AuraRadius.sm),
          onTap: onTap,
          child: Padding(
            padding: const EdgeInsets.symmetric(
              horizontal: AuraSpacing.md,
              vertical: AuraSpacing.sm + 2,
            ),
            child: Row(
              children: [
                Icon(
                  icon,
                  size: 18,
                  color: selected
                      ? palette.brandOnSurfaceText
                      : palette.textSecondary,
                ),
                const SizedBox(width: AuraSpacing.md),
                Expanded(
                  child: Text(
                    label,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      color: color,
                      fontSize: 13,
                      fontWeight: selected ? FontWeight.w600 : FontWeight.w500,
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class RouterTransitionNotifier extends ChangeNotifier {
  final Ref _ref;

  RouterTransitionNotifier(this._ref) {
    _ref.listen(authProvider, (previous, next) {
      notifyListeners();
    });
    _ref.listen(onboardingGateProvider, (previous, next) {
      notifyListeners();
    });
  }
}

/// Whether this Windows user chose the Desktop IDE as their landing screen
/// (Settings → Desktop IDE). Always false off Windows.
Future<bool> _prefersDesktopIde() async {
  if (!isDesktopIde) return false;
  final prefs = await SharedPreferences.getInstance();
  return prefs.getBool('desktop_use_ide') ?? false;
}

/// The app's redirect rules, apart from GoRouter so they can be tested.
///
/// Signed out, everything goes to `/login` -- including a session the backend
/// refused to renew, which `AuthNotifier.expireSession` ends so that this is
/// where the user lands (audit F-33).
///
/// The Desktop IDE preference picks where AURA *lands* -- the screen after
/// sign-in or onboarding -- and nothing more. It used to redirect every visit
/// to `/today` to `/ide`, so with the IDE on there was no way back to Home at
/// all, against ADR-015's "no forced redirect" (audit F-34).
Future<String?> resolveAppRedirect({
  required bool isLoggedIn,
  required bool? onboarded,
  required String location,
  required Future<bool> Function() prefersDesktopIde,
  bool hasPendingInvite = false,
}) async {
  final isLoggingIn = location == '/login';
  final isOnboarding = location == '/onboarding';

  Future<String> landing() async =>
      await prefersDesktopIde() ? '/ide' : '/today';

  if (!isLoggedIn && !isLoggingIn) return '/login';
  if (isLoggedIn && isLoggingIn) {
    if (onboarded == false) return '/onboarding';
    // An invite link opened while signed out picks up where it left off (SaaS P1-F2).
    return hasPendingInvite ? '/invite' : landing();
  }
  if (isLoggedIn && onboarded == false && !isOnboarding) {
    return '/onboarding';
  }
  if (isLoggedIn && onboarded == true && isOnboarding) {
    // Invitees are usually new: the invite waits through onboarding too (F2 review #2).
    return hasPendingInvite ? '/invite' : landing();
  }
  return null;
}

final routerProvider = Provider<GoRouter>((ref) {
  final notifier = RouterTransitionNotifier(ref);

  return GoRouter(
    initialLocation: '/today',
    refreshListenable: notifier,
    redirect: (context, state) async {
      final isLoggedIn = ref.read(authProvider).value != null;
      final inviteToken = state.uri.queryParameters['token'];
      if (!isLoggedIn && state.matchedLocation == '/invite' && inviteToken != null && inviteToken.isNotEmpty) {
        // Held in memory only, never in the URL we redirect to or on disk.
        ref.read(pendingInviteProvider.notifier).hold(inviteToken);
      }
      return resolveAppRedirect(
        isLoggedIn: isLoggedIn,
        hasPendingInvite: ref.read(pendingInviteProvider) != null,
        onboarded: ref.read(onboardingGateProvider),
        location: state.matchedLocation,
        prefersDesktopIde: _prefersDesktopIde,
      );
    },
    routes: [
      GoRoute(path: '/login', builder: (context, state) => const LoginPage()),
      GoRoute(
        path: '/onboarding',
        builder: (context, state) => const OnboardingPage(),
      ),
      GoRoute(path: '/home', redirect: (context, state) => '/today'),
      GoRoute(path: '/tasks', redirect: (context, state) => '/life?type=tasks'),
      GoRoute(
        path: '/calendar',
        redirect: (context, state) => '/life?type=calendar',
      ),
      GoRoute(
        path: '/finance',
        redirect: (context, state) => '/life?type=finance',
      ),
      GoRoute(
        path: '/health',
        redirect: (context, state) => '/life?type=health',
      ),
      GoRoute(path: '/settings', redirect: (context, state) => '/you'),
      GoRoute(path: '/learning', redirect: (context, state) => '/feedback'),
      GoRoute(
        path: '/voice',
        builder: (context, state) => const LiveVoicePage(),
      ),
      ShellRoute(
        builder: (context, state, child) => ShellLayout(child: child),
        routes: [
          GoRoute(
            path: '/today',
            builder: (context, state) => const HomePage(),
          ),
          GoRoute(
            path: '/life',
            builder: (context, state) => const LifeHubPage(),
          ),
          GoRoute(
            path: '/automations',
            builder: (context, state) => const AutomationsPage(),
          ),
          GoRoute(
            path: '/chat',
            builder: (context, state) =>
                ChatPage(initialQuery: chatInitialQueryFromExtra(state.extra)),
          ),
          GoRoute(
            path: '/you',
            builder: (context, state) => const SettingsPage(),
          ),
          GoRoute(path: '/plan', builder: (context, state) => const PlanPage()),
          GoRoute(path: '/sign-in-methods', builder: (context, state) => const LinkedAccountsPage()),
          GoRoute(path: '/invite', builder: (context, state) => InviteAcceptPage(token: state.uri.queryParameters['token'])),
          GoRoute(path: '/team-chats', builder: (context, state) => const SharedChatsPage()),
          GoRoute(
            path: '/team-chats/:sid',
            builder: (context, state) => SharedChatView(sharedId: state.pathParameters['sid']!),
          ),
          GoRoute(
            path: '/workspaces/:id',
            builder: (context, state) => TeamSettingsPage(workspaceId: state.pathParameters['id']!),
          ),
          GoRoute(
            path: '/profile',
            builder: (context, state) => const ProfilePage(),
          ),
          GoRoute(
            path: '/knowledge',
            builder: (context, state) => const KnowledgeCenterPage(),
          ),
          GoRoute(
            path: '/memory',
            builder: (context, state) => const MemoryPage(),
          ),
          GoRoute(
            path: '/feedback',
            builder: (context, state) => const FeedbackPage(),
          ),
          GoRoute(
            path: '/appearance',
            builder: (context, state) => const AppearancePage(),
          ),
          GoRoute(
            path: '/server-environment',
            builder: (context, state) => const BackendEnvironmentPage(),
          ),
          GoRoute(
            path: '/desktop-devices',
            builder: (context, state) => const DesktopDevicesPage(),
          ),
          GoRoute(
            path: '/analytics',
            builder: (context, state) => const AnalyticsPage(),
          ),
          GoRoute(
            path: '/models',
            builder: (context, state) => const ModelManagerPage(),
          ),
          GoRoute(
            path: '/engineer-history',
            builder: (context, state) => const EngineerHistoryPage(),
          ),
          GoRoute(
            path: '/github-connect',
            builder: (context, state) => const GithubConnectionPage(),
          ),
          GoRoute(
            path: '/engineer-daemon',
            builder: (context, state) => const HostDaemonPage(),
          ),
          GoRoute(
            path: '/engineer-daemon/browse',
            builder: (context, state) =>
                HostFileBrowserPage(root: state.extra as String),
          ),
          GoRoute(
            path: '/projects',
            builder: (context, state) => const ProjectsPage(),
          ),
          GoRoute(
            path: '/goals',
            builder: (context, state) => const GoalsPage(),
          ),
        ],
      ),

      // Desktop IDE Shell Route (Windows only)
      ShellRoute(
        builder: (context, state, child) =>
            isDesktopIde ? DesktopShell(child: child) : const SizedBox.shrink(),
        routes: [
          GoRoute(
            path: '/ide',
            builder: (context, state) => const WorkspaceWelcomePage(),
          ),
          GoRoute(
            path: '/ide/workspace',
            builder: (context, state) => const WorkspaceManagerPage(),
          ),
        ],
      ),
    ],
  );
});
