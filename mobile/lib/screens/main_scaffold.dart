import 'package:flutter/material.dart';

import '../util/responsive.dart';
import '../widgets/bottom_nav_bar.dart';
import 'analytics/analytics_page.dart';
import 'area_selection_screen.dart';
import 'placeholder_tab.dart';

/// App shell: a persistent bottom navigation bar over five tabs.
///
/// Each tab keeps its own state via [IndexedStack]. The Analytics tab hosts a
/// nested [Navigator] so its detail / image-options / area-selection pages push
/// on top while the bottom bar stays visible.
class MainScaffold extends StatefulWidget {
  const MainScaffold({super.key});

  @override
  State<MainScaffold> createState() => _MainScaffoldState();
}

class _MainScaffoldState extends State<MainScaffold> {
  static const int _analyticsIndex = 2;

  int _index = 0;
  final GlobalKey<NavigatorState> _analyticsNav = GlobalKey<NavigatorState>();

  late final List<Widget> _tabs = [
    const AreaSelectionScreen(), // New Image
    const PlaceholderTab(title: 'Explore', icon: Icons.public),
    Navigator(
      key: _analyticsNav,
      onGenerateRoute: (settings) =>
          MaterialPageRoute(builder: (_) => const AnalyticsPage()),
    ),
    const PlaceholderTab(title: 'My Profile', icon: Icons.person_outline),
    const PlaceholderTab(title: 'My Cart', icon: Icons.shopping_cart_outlined),
  ];

  void _onTap(int i) {
    // Re-tapping the active Analytics tab returns to the catalog root.
    if (i == _index && i == _analyticsIndex) {
      _analyticsNav.currentState?.popUntil((r) => r.isFirst);
    }
    setState(() => _index = i);
  }

  @override
  Widget build(BuildContext context) {
    final body = IndexedStack(index: _index, children: _tabs);

    // Wide screens (tablet / desktop / web) get a left navigation rail; phones
    // keep the black bottom bar. Each tab preserves its state across the switch
    // because the IndexedStack is shared.
    if (context.isWide) {
      return Scaffold(
        backgroundColor: Colors.black,
        body: SafeArea(
          child: Row(
            children: [
              AppNavRail(currentIndex: _index, onTap: _onTap),
              const VerticalDivider(width: 1, color: Colors.white12),
              Expanded(child: body),
            ],
          ),
        ),
      );
    }

    return Scaffold(
      backgroundColor: Colors.black,
      body: body,
      bottomNavigationBar: AppBottomNavBar(
        currentIndex: _index,
        onTap: _onTap,
      ),
    );
  }
}
