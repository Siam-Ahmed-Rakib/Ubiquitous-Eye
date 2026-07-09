import 'package:flutter/material.dart';

const Color _accent = Color(0xFFEF9A3D);
const Color _inactive = Color(0xFF8A8A8E);

/// One destination shared by the phone bottom bar and the wide-screen rail.
class NavDestinationData {
  final String label;
  final IconData icon;
  const NavDestinationData(this.label, this.icon);
}

/// The app's five primary destinations, in tab order.
const List<NavDestinationData> kNavDestinations = [
  NavDestinationData('New Image', Icons.satellite_alt),
  NavDestinationData('Explore', Icons.public),
  NavDestinationData('Analytics', Icons.bar_chart_rounded),
  NavDestinationData('My Profile', Icons.person_outline),
  NavDestinationData('My Cart', Icons.shopping_cart_outlined),
];

/// The black bottom navigation bar shown on phones.
/// Navigation logic is wired later — for now tapping only moves the highlight.
class AppBottomNavBar extends StatelessWidget {
  final int currentIndex;
  final ValueChanged<int> onTap;

  const AppBottomNavBar({
    super.key,
    required this.currentIndex,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      color: Colors.black,
      child: SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 8),
          child: Row(
            children: List.generate(kNavDestinations.length, (i) {
              final selected = i == currentIndex;
              final color = selected ? _accent : _inactive;
              return Expanded(
                child: InkWell(
                  onTap: () => onTap(i),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(kNavDestinations[i].icon, color: color, size: 24),
                      const SizedBox(height: 4),
                      Text(
                        kNavDestinations[i].label,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        textAlign: TextAlign.center,
                        style: TextStyle(
                          color: color,
                          fontSize: 11,
                          fontWeight:
                              selected ? FontWeight.w600 : FontWeight.w400,
                        ),
                      ),
                    ],
                  ),
                ),
              );
            }),
          ),
        ),
      ),
    );
  }
}

/// The wide-screen (tablet / desktop) equivalent of [AppBottomNavBar]: a black
/// [NavigationRail] pinned to the left edge that scrolls if the window is short.
class AppNavRail extends StatelessWidget {
  final int currentIndex;
  final ValueChanged<int> onTap;

  const AppNavRail({
    super.key,
    required this.currentIndex,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        return SingleChildScrollView(
          child: ConstrainedBox(
            constraints: BoxConstraints(minHeight: constraints.maxHeight),
            child: IntrinsicHeight(
              child: NavigationRail(
                backgroundColor: Colors.black,
                selectedIndex: currentIndex,
                onDestinationSelected: onTap,
                labelType: NavigationRailLabelType.all,
                indicatorColor: Colors.white10,
                selectedIconTheme: const IconThemeData(color: _accent),
                unselectedIconTheme: const IconThemeData(color: _inactive),
                selectedLabelTextStyle: const TextStyle(
                  color: _accent,
                  fontSize: 12,
                  fontWeight: FontWeight.w600,
                ),
                unselectedLabelTextStyle: const TextStyle(
                  color: _inactive,
                  fontSize: 12,
                ),
                destinations: [
                  for (final d in kNavDestinations)
                    NavigationRailDestination(
                      icon: Icon(d.icon),
                      label: Text(d.label),
                    ),
                ],
              ),
            ),
          ),
        );
      },
    );
  }
}
