import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../features/auth/presentation/login_page.dart';
import '../../features/auth/presentation/auth_provider.dart';
import '../../features/tasks/presentation/tasks_page.dart';
import '../../features/calendar/presentation/calendar_page.dart';
import '../../features/finance/presentation/finance_page.dart';
import '../../features/health/presentation/health_page.dart';
import '../../features/automations/presentation/automations_page.dart';
import '../../features/chat/presentation/chat_page.dart';

// Navigation Shell synchronized with GoRouter paths
class ShellLayout extends StatelessWidget {
  final Widget child;
  const ShellLayout({super.key, required this.child});

  int _calculateSelectedIndex(BuildContext context) {
    final location = GoRouterState.of(context).uri.toString();
    if (location.startsWith('/tasks')) return 0;
    if (location.startsWith('/calendar')) return 1;
    if (location.startsWith('/finance')) return 2;
    if (location.startsWith('/health')) return 3;
    if (location.startsWith('/automations')) return 4;
    if (location.startsWith('/chat')) return 5;
    return 0;
  }

  void _onDestinationSelected(BuildContext context, int index) {
    switch (index) {
      case 0:
        context.go('/tasks');
        break;
      case 1:
        context.go('/calendar');
        break;
      case 2:
        context.go('/finance');
        break;
      case 3:
        context.go('/health');
        break;
      case 4:
        context.go('/automations');
        break;
      case 5:
        context.go('/chat');
        break;
    }
  }

  @override
  Widget build(BuildContext context) {
    final selectedIndex = _calculateSelectedIndex(context);

    return Scaffold(
      body: Row(
        children: [
          NavigationRail(
            backgroundColor: const Color(0xFF0F0C1B),
            selectedIconTheme: const IconThemeData(color: Color(0xFF6366F1)),
            unselectedIconTheme: const IconThemeData(color: Color(0xFF94A3B8)),
            selectedLabelTextStyle: const TextStyle(color: Color(0xFF6366F1)),
            unselectedLabelTextStyle: const TextStyle(color: Color(0xFF94A3B8)),
            destinations: const [
              NavigationRailDestination(
                icon: Icon(Icons.check_circle_outline),
                selectedIcon: Icon(Icons.check_circle),
                label: Text('Tasks'),
              ),
              NavigationRailDestination(
                icon: Icon(Icons.calendar_month_outlined),
                selectedIcon: Icon(Icons.calendar_month),
                label: Text('Calendar'),
              ),
              NavigationRailDestination(
                icon: Icon(Icons.monetization_on_outlined),
                selectedIcon: Icon(Icons.monetization_on),
                label: Text('Finance'),
              ),
              NavigationRailDestination(
                icon: Icon(Icons.favorite_outline),
                selectedIcon: Icon(Icons.favorite),
                label: Text('Health'),
              ),
              NavigationRailDestination(
                icon: Icon(Icons.settings_suggest_outlined),
                selectedIcon: Icon(Icons.settings_suggest),
                label: Text('Automations'),
              ),
              NavigationRailDestination(
                icon: Icon(Icons.chat_bubble_outline),
                selectedIcon: Icon(Icons.chat_bubble),
                label: Text('Chat'),
              ),
            ],
            selectedIndex: selectedIndex,
            onDestinationSelected: (index) => _onDestinationSelected(context, index),
            labelType: NavigationRailLabelType.all,
          ),
          const VerticalDivider(thickness: 1, width: 1, color: Color(0x1F94A3B8)),
          Expanded(child: child),
        ],
      ),
    );
  }
}

final routerProvider = Provider<GoRouter>((ref) {
  final authState = ref.watch(authProvider);

  return GoRouter(
    initialLocation: '/tasks',
    redirect: (context, state) {
      final isLoggedIn = authState.value != null;
      final isLoggingIn = state.matchedLocation == '/login';

      if (!isLoggedIn && !isLoggingIn) {
        return '/login';
      }
      if (isLoggedIn && isLoggingIn) {
        return '/tasks';
      }
      return null;
    },
    routes: [
      GoRoute(
        path: '/login',
        builder: (context, state) => const LoginPage(),
      ),
      ShellRoute(
        builder: (context, state, child) => ShellLayout(child: child),
        routes: [
          GoRoute(
            path: '/tasks',
            builder: (context, state) => const TasksPage(),
          ),
          GoRoute(
            path: '/calendar',
            builder: (context, state) => const CalendarPage(),
          ),
          GoRoute(
            path: '/finance',
            builder: (context, state) => const FinancePage(),
          ),
          GoRoute(
            path: '/health',
            builder: (context, state) => const HealthPage(),
          ),
          GoRoute(
            path: '/automations',
            builder: (context, state) => const AutomationsPage(),
          ),
          GoRoute(
            path: '/chat',
            builder: (context, state) => const ChatPage(),
          ),
        ],
      ),
    ],
  );
});
