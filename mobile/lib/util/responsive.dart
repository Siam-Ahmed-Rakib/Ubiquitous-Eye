import 'package:flutter/widgets.dart';

/// Width breakpoints used across the app to switch between phone, tablet, and
/// desktop layouts. Values are logical pixels (density-independent).
class Breakpoints {
  Breakpoints._();

  /// At/above this width we treat the screen as at least a tablet.
  static const double tablet = 600;

  /// At/above this width we treat the screen as a desktop / wide web window.
  static const double desktop = 1024;
}

/// Coarse device class derived from the current width.
enum ScreenType { phone, tablet, desktop }

/// Adaptive-layout helpers hanging off [BuildContext] so any widget can ask
/// about the current screen without threading [MediaQuery] through manually.
extension ResponsiveContext on BuildContext {
  Size get screenSize => MediaQuery.sizeOf(this);
  double get screenWidth => screenSize.width;
  double get screenHeight => screenSize.height;

  ScreenType get screenType {
    final w = screenWidth;
    if (w >= Breakpoints.desktop) return ScreenType.desktop;
    if (w >= Breakpoints.tablet) return ScreenType.tablet;
    return ScreenType.phone;
  }

  bool get isPhone => screenType == ScreenType.phone;
  bool get isTablet => screenType == ScreenType.tablet;
  bool get isDesktop => screenType == ScreenType.desktop;

  /// True for tablets and desktops — the point at which we switch to a side
  /// navigation rail and centre/constrain page content.
  bool get isWide => screenWidth >= Breakpoints.tablet;

  /// Selects a value by screen class, falling back to the next-smaller value
  /// when a larger one isn't supplied.
  ///
  /// ```dart
  /// final columns = context.responsive(phone: 2, tablet: 3, desktop: 4);
  /// ```
  T responsive<T>({required T phone, T? tablet, T? desktop}) =>
      switch (screenType) {
        ScreenType.desktop => desktop ?? tablet ?? phone,
        ScreenType.tablet => tablet ?? phone,
        ScreenType.phone => phone,
      };

  /// A gentle multiplier for spacing / sizing that grows with the device's
  /// shorter side, clamped so small phones and large desktops both stay
  /// sensible. Baseline 360 is a common compact-phone width.
  double get uiScale => (screenSize.shortestSide / 360).clamp(1.0, 1.3).toDouble();
}

/// Horizontally centres [child] and caps it at [maxWidth] so page content
/// doesn't stretch edge-to-edge on tablets and wide desktop windows. On phones
/// (where the available width is below [maxWidth]) it's a no-op passthrough.
class ResponsiveCenter extends StatelessWidget {
  final double maxWidth;
  final EdgeInsetsGeometry padding;
  final Alignment alignment;
  final Widget child;

  const ResponsiveCenter({
    super.key,
    this.maxWidth = 640,
    this.padding = EdgeInsets.zero,
    this.alignment = Alignment.topCenter,
    required this.child,
  });

  @override
  Widget build(BuildContext context) {
    return Align(
      alignment: alignment,
      child: ConstrainedBox(
        constraints: BoxConstraints(maxWidth: maxWidth),
        child: Padding(padding: padding, child: child),
      ),
    );
  }
}
