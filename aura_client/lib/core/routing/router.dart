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
import '../../features/engineering/presentation/engineer_history_page.dart';
import '../../features/engineering/presentation/github_connection_page.dart';
import '../../features/feedback/presentation/feedback_page.dart';
import '../../features/home/presentation/home_page.dart';
import '../../features/knowledge/presentation/knowledge_center_page.dart';
import '../../features/life_hub/presentation/life_hub_page.dart';
import '../../features/memory/presentation/memory_page.dart';
import '../../features/models/presentation/model_manager_page.dart';
import '../../features/onboarding/presentation/onboarding_gate.dart';
import '../../features/onboarding/presentation/onboarding_page.dart';
import '../../features/settings/presentation/backend_environment_page.dart';
import '../../features/settings/presentation/appearance_page.dart';
import '../../features/settings/presentation/settings_page.dart';
import '../../l10n/generated/app_localizations.dart';
import '../../features/voice_live/presentation/live_voice_page.dart';
import '../design_system/design_system.dart';

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
    final isMobile = MediaQuery.of(context).size.width < 768;
    final palette = AuraPalette.of(context);
    final l10n = AppLocalizations.of(context);

    final shortcuts = <ShortcutActivator, VoidCallback>{
      const SingleActivator(LogicalKeyboardKey.keyK, control: true):
          () => showCommandPalette(context, ref),
      const SingleActivator(LogicalKeyboardKey.keyK, meta: true):
          () => showCommandPalette(context, ref),
    };

    final fab = _showCaptureFab(context)
        ? FloatingActionButton(
            heroTag: 'capture_fab',
            tooltip: 'Ask Copilot',
            backgroundColor: palette.brand,
            foregroundColor: palette.onBrand,
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
            body: SafeArea(child: child),
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
                labelType: NavigationRailLabelType.all,
                leading: Padding(
                  padding: const EdgeInsets.symmetric(vertical: AuraSpacing.lg),
                  child: Column(
                    children: [
                      ClipRRect(
                        borderRadius: BorderRadius.circular(AuraRadius.sm),
                        child: Image.asset(
                          'assets/app_icon.png',
                          width: 42,
                          height: 42,
                          fit: BoxFit.contain,
                        ),
                      ),
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
                ),
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
              Expanded(child: child),
            ],
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

final routerProvider = Provider<GoRouter>((ref) {
  final notifier = RouterTransitionNotifier(ref);

  return GoRouter(
    initialLocation: '/today',
    refreshListenable: notifier,
    redirect: (context, state) {
      final authState = ref.read(authProvider);
      final isLoggedIn = authState.value != null;
      final location = state.matchedLocation;
      final isLoggingIn = location == '/login';
      final isOnboarding = location == '/onboarding';
      final onboarded = ref.read(onboardingGateProvider);

      if (!isLoggedIn && !isLoggingIn) return '/login';
      if (isLoggedIn && isLoggingIn) {
        return onboarded == false ? '/onboarding' : '/today';
      }
      if (isLoggedIn && onboarded == false && !isOnboarding) {
        return '/onboarding';
      }
      if (isLoggedIn && onboarded == true && isOnboarding) {
        return '/today';
      }
      return null;
    },
    routes: [
      GoRoute(
        path: '/login',
        builder: (context, state) => const LoginPage(),
      ),
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
      GoRoute(path: '/health', redirect: (context, state) => '/life?type=health'),
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
            builder: (context, state) => ChatPage(
              initialQuery: chatInitialQueryFromExtra(state.extra),
            ),
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
        ],
      ),
    ],
  );
});
