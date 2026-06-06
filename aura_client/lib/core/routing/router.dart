import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../theme/theme.dart';
import '../../features/auth/presentation/login_page.dart';
import '../../features/auth/presentation/auth_provider.dart';
import '../../features/home/presentation/home_page.dart';
import '../../features/tasks/presentation/tasks_page.dart';
import '../../features/calendar/presentation/calendar_page.dart';
import '../../features/finance/presentation/finance_page.dart';
import '../../features/health/presentation/health_page.dart';
import '../../features/automations/presentation/automations_page.dart';
import '../../features/chat/presentation/chat_page.dart';
import '../../features/settings/presentation/settings_page.dart';
import '../../features/auth/presentation/profile_page.dart';
import '../../features/knowledge/presentation/knowledge_center_page.dart';
import '../../features/analytics/presentation/analytics_page.dart';
import '../../features/models/presentation/model_manager_page.dart';
import '../../features/learning/presentation/learning_dashboard.dart';

// Navigation Shell synchronized with GoRouter paths
class ShellLayout extends StatelessWidget {
  final Widget child;
  const ShellLayout({super.key, required this.child});

  int _calculateSelectedIndex(BuildContext context) {
    final location = GoRouterState.of(context).uri.toString();
    if (location.startsWith('/home')) return 0;
    if (location.startsWith('/tasks')) return 1;
    if (location.startsWith('/calendar')) return 2;
    if (location.startsWith('/finance')) return 3;
    if (location.startsWith('/health')) return 4;
    if (location.startsWith('/chat')) return 5;
    return 0;
  }

  void _onDestinationSelected(BuildContext context, int index) {
    switch (index) {
      case 0:
        context.go('/home');
        break;
      case 1:
        context.go('/tasks');
        break;
      case 2:
        context.go('/calendar');
        break;
      case 3:
        context.go('/finance');
        break;
      case 4:
        context.go('/health');
        break;
      case 5:
        context.go('/chat');
        break;
    }
  }

  @override
  Widget build(BuildContext context) {
    final selectedIndex = _calculateSelectedIndex(context);
    final isMobile = MediaQuery.of(context).size.width < 768;

    if (isMobile) {
      return Scaffold(
        body: SafeArea(child: child),
        bottomNavigationBar: BottomNavigationBar(
          backgroundColor: const Color(0xFF0F0C1B),
          selectedItemColor: const Color(0xFF6366F1),
          unselectedItemColor: const Color(0xFF94A3B8),
          currentIndex: selectedIndex,
          type: BottomNavigationBarType.fixed,
          selectedFontSize: 11,
          unselectedFontSize: 11,
          selectedLabelStyle: const TextStyle(fontWeight: FontWeight.w600),
          unselectedLabelStyle: const TextStyle(fontWeight: FontWeight.normal),
          onTap: (index) => _onDestinationSelected(context, index),
          items: const [
            BottomNavigationBarItem(
              icon: Icon(Icons.home_outlined),
              activeIcon: Icon(Icons.home),
              label: 'Home',
            ),
            BottomNavigationBarItem(
              icon: Icon(Icons.check_circle_outline),
              activeIcon: Icon(Icons.check_circle),
              label: 'Tasks',
            ),
            BottomNavigationBarItem(
              icon: Icon(Icons.calendar_month_outlined),
              activeIcon: Icon(Icons.calendar_month),
              label: 'Calendar',
            ),
            BottomNavigationBarItem(
              icon: Icon(Icons.monetization_on_outlined),
              activeIcon: Icon(Icons.monetization_on),
              label: 'Finance',
            ),
            BottomNavigationBarItem(
              icon: Icon(Icons.favorite_outline),
              activeIcon: Icon(Icons.favorite),
              label: 'Health',
            ),
            BottomNavigationBarItem(
              icon: Icon(Icons.chat_bubble_outline),
              activeIcon: Icon(Icons.chat_bubble),
              label: 'Chat',
            ),
          ],
        ),
      );
    }

    return Scaffold(
      body: Row(
        children: [
          NavigationRail(
            backgroundColor: const Color(0xFF0F0C1B),
            selectedIconTheme: const IconThemeData(color: Color(0xFF6366F1)),
            unselectedIconTheme: const IconThemeData(color: Color(0xFF94A3B8)),
            selectedLabelTextStyle: const TextStyle(color: Color(0xFF6366F1)),
            unselectedLabelTextStyle: const TextStyle(color: Color(0xFF94A3B8)),
            leading: Column(
              children: [
                const SizedBox(height: 16),
                Container(
                  width: 42,
                  height: 42,
                  decoration: BoxDecoration(
                    borderRadius: BorderRadius.circular(10),
                    boxShadow: [
                      BoxShadow(
                        color: const Color(0xFF6366F1).withOpacity(0.3),
                        blurRadius: 8,
                        offset: const Offset(0, 3),
                      ),
                    ],
                  ),
                  child: ClipRRect(
                    borderRadius: BorderRadius.circular(10),
                    child: Image.asset(
                      'assets/app_icon.png',
                      fit: BoxFit.contain,
                    ),
                  ),
                ),
                const SizedBox(height: 8),
                const Text(
                  'AURA AI',
                  style: TextStyle(
                    fontSize: 10,
                    fontWeight: FontWeight.w900,
                    color: Colors.white,
                    letterSpacing: 1.0,
                  ),
                ),
                const SizedBox(height: 16),
              ],
            ),
            destinations: const [
              NavigationRailDestination(
                icon: Icon(Icons.home_outlined),
                selectedIcon: Icon(Icons.home),
                label: Text('Home'),
              ),
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

class RouterTransitionNotifier extends ChangeNotifier {
  final Ref _ref;

  RouterTransitionNotifier(this._ref) {
    _ref.listen(authProvider, (previous, next) {
      notifyListeners();
    });
  }
}

final routerProvider = Provider<GoRouter>((ref) {
  final notifier = RouterTransitionNotifier(ref);

  return GoRouter(
    initialLocation: '/home',
    refreshListenable: notifier,
    redirect: (context, state) {
      final authState = ref.read(authProvider);
      final isLoggedIn = authState.value != null;
      final isLoggingIn = state.matchedLocation == '/login';

      if (!isLoggedIn && !isLoggingIn) {
        return '/login';
      }
      if (isLoggedIn && isLoggingIn) {
        return '/home';
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
            path: '/home',
            builder: (context, state) => const HomePage(),
          ),
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
          GoRoute(
            path: '/settings',
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
            path: '/analytics',
            builder: (context, state) => const AnalyticsPage(),
          ),
          GoRoute(
            path: '/models',
            builder: (context, state) => const ModelManagerPage(),
          ),
          GoRoute(
            path: '/learning',
            builder: (context, state) => const LearningDashboard(),
          ),
        ],
      ),
    ],
  );
});
