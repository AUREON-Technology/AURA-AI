import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../features/analytics/presentation/analytics_page.dart';
import '../../features/auth/presentation/auth_provider.dart';
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
import '../../features/knowledge/presentation/knowledge_center_page.dart';
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

  /// The shell's tabs. The Windows desktop app is the IDE and You; every other
  /// build is the one AURA app: Copilot, Engineer and You.
  static List<({IconData icon, IconData selected, String label})> _tabs(
    AppLocalizations? l10n,
  ) {
    if (isDesktopIde) {
      return [
        (
          icon: Icons.dashboard_customize_outlined,
          selected: Icons.dashboard_customize,
          label: l10n?.settingsDesktopIdeTitle ?? 'Desktop IDE',
        ),
        (
          icon: Icons.person_outline,
          selected: Icons.person,
          label: l10n?.navYou ?? 'You',
        ),
      ];
    }
    return [
      (
        icon: Icons.auto_awesome_outlined,
        selected: Icons.auto_awesome,
        label: l10n?.navCopilot ?? 'Copilot',
      ),
      (
        icon: Icons.code_outlined,
        selected: Icons.code,
        label: l10n?.settingsEngineerTitle ?? 'Engineer',
      ),
      (
        icon: Icons.person_outline,
        selected: Icons.person,
        label: l10n?.navYou ?? 'You',
      ),
    ];
  }

  /// Engineer's pages, which the Engineer tab opens.
  static bool _isEngineerPath(String path) =>
      path.startsWith('/engineer-history') ||
      path.startsWith('/github-connect') ||
      path.startsWith('/engineer-daemon');

  int _calculateSelectedIndex(BuildContext context) {
    final path = GoRouterState.of(context).uri.path;
    // The desktop shell only ever shows the IDE and You; You is index 1 there.
    if (isDesktopIde) return 1;
    if (path.startsWith('/chat')) return 0;
    if (_isEngineerPath(path)) return 1;
    return 2;
  }

  void _onDestinationSelected(BuildContext context, int index) {
    if (isDesktopIde) {
      context.go(index == 0 ? '/ide' : '/you');
      return;
    }
    switch (index) {
      case 0:
        context.go('/chat');
      case 1:
        context.go('/engineer-history');
      case 2:
        context.go('/you');
    }
  }

  bool _showCaptureFab(BuildContext context) {
    final path = GoRouterState.of(context).uri.path;
    return !path.startsWith('/chat');
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
                for (final tab in _tabs(l10n))
                  NavigationDestination(
                    icon: Icon(tab.icon),
                    selectedIcon: Icon(tab.selected),
                    label: tab.label,
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
                  for (final tab in _tabs(l10n))
                    NavigationRailDestination(
                      icon: Icon(tab.icon),
                      selectedIcon: Icon(tab.selected),
                      label: Text(tab.label),
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

/// The app's redirect rules, apart from GoRouter so they can be tested.
///
/// Signed out, everything goes to `/login` -- including a session the backend
/// refused to renew, which `AuthNotifier.expireSession` ends so that this is
/// where the user lands (audit F-33).
///
/// The landing screen is the platform's home: Copilot on mobile, the IDE on
/// Windows. It is where AURA lands after sign-in or onboarding. A screen the
/// build does not show (Today, Life Hub, or the IDE on mobile) is sent there
/// too, see [isReachableRoute].
Future<String?> resolveAppRedirect({
  required bool isLoggedIn,
  required bool? onboarded,
  required String location,
  bool? desktop,
}) async {
  final isDesktop = desktop ?? isDesktopIde;
  final landing = landingRouteFor(desktop: isDesktop);
  final isLoggingIn = location == '/login';
  final isOnboarding = location == '/onboarding';

  if (!isLoggedIn && !isLoggingIn) return '/login';
  if (isLoggedIn && isLoggingIn) {
    return onboarded == false ? '/onboarding' : landing;
  }
  if (isLoggedIn && onboarded == false && !isOnboarding) {
    return '/onboarding';
  }
  if (isLoggedIn && onboarded == true && isOnboarding) {
    return landing;
  }
  if (isLoggedIn && !isReachableRoute(location, desktop: isDesktop)) {
    return landing;
  }
  return null;
}

/// Screens the Windows desktop app shows besides the IDE: account and
/// settings, nothing else.
const _desktopScreens = ['/you', '/profile', '/appearance', '/server-environment'];

/// Whether a signed-in build shows this screen. The mobile app has Chat and
/// Engineer, and no IDE; Today and Life Hub are hidden. The desktop app is
/// the IDE and the screens in `_desktopScreens`. Anything else is sent to the
/// landing screen.
bool isReachableRoute(String location, {required bool desktop}) {
  bool under(String root) => location == root || location.startsWith('$root/');
  if (desktop) {
    return under('/ide') || _desktopScreens.any(under);
  }
  if (under('/ide')) return false;
  return !(under('/today') || under('/life'));
}

final routerProvider = Provider<GoRouter>((ref) {
  final notifier = RouterTransitionNotifier(ref);

  return GoRouter(
    initialLocation: appLandingRoute,
    refreshListenable: notifier,
    redirect: (context, state) async {
      return resolveAppRedirect(
        isLoggedIn: ref.read(authProvider).value != null,
        onboarded: ref.read(onboardingGateProvider),
        location: state.matchedLocation,
      );
    },
    routes: [
      GoRoute(path: '/login', builder: (context, state) => const LoginPage()),
      GoRoute(
        path: '/onboarding',
        builder: (context, state) => const OnboardingPage(),
      ),
      GoRoute(path: '/home', redirect: (context, state) => '/today'),
      // Today and Life Hub are hidden. Their old links land on the platform's
      // home screen (Copilot on mobile, the IDE on Windows).
      GoRoute(path: '/today', redirect: (context, state) => appLandingRoute),
      GoRoute(path: '/life', redirect: (context, state) => appLandingRoute),
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
