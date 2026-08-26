/// Layout-regression tests: render the app's screens across the device sizes we
/// support — and at the largest text scale [UbiquitousEyeApp] allows — asserting
/// nothing overflows.
///
/// Overflow is a *paint-time* error in Flutter: it never throws, it just paints
/// a yellow-and-black stripe and logs. These tests capture [FlutterError.onError]
/// so those logs become failures.
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:latlong2/latlong.dart';
import 'package:ubiquitous_eye/main.dart';
import 'package:ubiquitous_eye/models/analytics_service.dart';
import 'package:ubiquitous_eye/models/area_bounds.dart';
import 'package:ubiquitous_eye/screens/analysis/analysis_run_screen.dart';
import 'package:ubiquitous_eye/screens/analysis/land_use_screen.dart';
import 'package:ubiquitous_eye/screens/analytics/analytics_image_options_page.dart';
import 'package:ubiquitous_eye/screens/analytics/analytics_page.dart';
import 'package:ubiquitous_eye/screens/analytics/service_detail_page.dart';
import 'package:ubiquitous_eye/screens/area_selection_screen.dart';
import 'package:ubiquitous_eye/screens/placeholder_tab.dart';
import 'package:ubiquitous_eye/widgets/bottom_nav_bar.dart';
import 'package:ubiquitous_eye/widgets/category_filter_bar.dart';

/// A small area over Dhaka — the same default the app starts on.
final AreaBounds _testBounds = AreaBounds.square(
  center: const LatLng(23.7806, 90.3998),
  sizeKm: 5,
);

/// Screen sizes in logical pixels, spanning the range we claim to support.
/// 320x568 is the smallest phone still in circulation (iPhone SE 1st gen) and
/// is where fixed-height layouts break first.
const Map<String, Size> kSizes = {
  'small phone (320x568)': Size(320, 568),
  'compact phone (360x640)': Size(360, 640),
  'modern phone (390x844)': Size(390, 844),
  'large phone (430x932)': Size(430, 932),
  'phone landscape (844x390)': Size(844, 390),
  'tablet (768x1024)': Size(768, 1024),
  'tablet landscape (1024x768)': Size(1024, 768),
  'desktop (1440x900)': Size(1440, 900),
};

/// 1.0 is the default; 1.3 is the ceiling `UbiquitousEyeApp` clamps OS text
/// scaling to, so it is the worst case any user can actually produce.
const List<double> kTextScales = [1.0, 1.3];

/// Pumps [build] at [size]/[textScale] and returns every layout error raised.
///
/// Two subtleties make the naive version silently pass:
///  * Text scale must be set on the *platform dispatcher*, not by wrapping the
///    widget — `MaterialApp` builds its own `MediaQuery` from the view and would
///    discard an outer one.
///  * Overflow is reported during **paint**. Pumping a structurally identical
///    tree reuses the elements and repaints nothing, so the second and later
///    configurations would report clean regardless of their size. Tearing the
///    tree down between runs forces a real layout+paint each time.
Future<List<String>> _layoutErrors(
  WidgetTester tester,
  Size size,
  double textScale,
  Widget Function() build, {
  Future<void> Function(WidgetTester)? interact,
}) async {
  tester.view.devicePixelRatio = 1.0;
  tester.view.physicalSize = size;
  tester.platformDispatcher.textScaleFactorTestValue = textScale;
  addTearDown(tester.view.reset);
  addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);

  // Discard the previous tree so the next pump genuinely re-lays-out and repaints.
  await tester.pumpWidget(const SizedBox.shrink());

  final errors = <String>[];
  final previous = FlutterError.onError;
  FlutterError.onError = (details) {
    final text = details.exceptionAsString();
    // Tile fetches fail in the sandbox; only layout problems matter here.
    if (text.contains('overflowed')) {
      errors.add(text.split('\n').first.trim());
    }
  };

  try {
    await tester.pumpWidget(build());
    await tester.pump(const Duration(milliseconds: 350));
    if (interact != null) {
      await interact(tester);
      await tester.pump(const Duration(milliseconds: 350));
    }
  } finally {
    FlutterError.onError = previous;
  }
  // Drain anything the binding queued so it can't fail a later test.
  while (tester.takeException() != null) {}
  return errors.toSet().toList();
}

/// Both analysis screens offer this, and it fills the screen with a result
/// without touching the network — the only way to exercise the results layout.
const String kSampleButtonLabel = 'Load sample result (no backend needed)';

/// Taps the "load sample" button, bringing the screen to its results state.
Future<void> _loadSampleResult(WidgetTester tester) async {
  final button = find.text(kSampleButtonLabel);
  if (button.evaluate().isEmpty) return;
  await tester.ensureVisible(button);
  await tester.pump();
  await tester.tap(button, warnIfMissed: false);
  // The land-use sample decodes a raster; give it room to arrive.
  for (var i = 0; i < 6; i++) {
    await tester.pump(const Duration(milliseconds: 120));
  }
}

/// Runs [build] across every size x text-scale combination and reports all
/// failures together, so one run tells you the full picture.
void _expectNoOverflowAnywhere(
  String label,
  Widget Function() build, {
  Future<void> Function(WidgetTester)? interact,
}) {
  testWidgets('$label survives every screen size and text scale',
      (tester) async {
    final failures = <String>[];
    for (final entry in kSizes.entries) {
      for (final scale in kTextScales) {
        final errors = await _layoutErrors(
          tester,
          entry.value,
          scale,
          build,
          interact: interact,
        );
        for (final e in errors) {
          failures.add('  ${entry.key} @ ${scale}x -> $e');
        }
      }
    }
    expect(
      failures,
      isEmpty,
      reason: '$label overflowed in ${failures.length} configuration(s):\n'
          '${failures.join('\n')}',
    );
  });
}

Widget _wrap(Widget child) => MaterialApp(
      debugShowCheckedModeBanner: false,
      home: child,
    );

void main() {
  _expectNoOverflowAnywhere(
    'Analytics catalog',
    () => _wrap(const AnalyticsPage()),
  );

  _expectNoOverflowAnywhere(
    'Category filter bar',
    () => _wrap(
      Scaffold(body: CategoryFilterBar(selected: null, onSelected: (_) {})),
    ),
  );

  _expectNoOverflowAnywhere(
    'Bottom navigation bar',
    () => _wrap(
      Scaffold(
        bottomNavigationBar: AppBottomNavBar(currentIndex: 0, onTap: (_) {}),
      ),
    ),
  );

  _expectNoOverflowAnywhere(
    'Service detail page',
    () => _wrap(ServiceDetailPage(service: kAnalyticsServices.first)),
  );

  _expectNoOverflowAnywhere(
    'Image options page',
    () => _wrap(AnalyticsImageOptionsPage(service: kAnalyticsServices.first)),
  );

  _expectNoOverflowAnywhere(
    'Area selection screen',
    () => _wrap(const AreaSelectionScreen(showBackButton: true)),
  );

  // The two analysis screens in their initial (pre-run) state: date pickers and
  // the Run action. The network call is never made, so this is the form layout.
  _expectNoOverflowAnywhere(
    'Change-detection run screen',
    () => _wrap(AnalysisRunScreen(
      bounds: _testBounds,
      service: kAnalyticsServices.first,
    )),
  );

  _expectNoOverflowAnywhere(
    'Land use screen',
    () => _wrap(LandUseScreen(
      bounds: _testBounds,
      service: kAnalyticsServices.firstWhere(
        (s) => s.id == 'land_use_classification',
      ),
    )),
  );

  // …and the same two screens showing a finished result: banner, statistics,
  // legend and (for land use) the class breakdown.
  _expectNoOverflowAnywhere(
    'Change-detection results',
    () => _wrap(AnalysisRunScreen(
      bounds: _testBounds,
      service: kAnalyticsServices.first,
    )),
    interact: _loadSampleResult,
  );

  _expectNoOverflowAnywhere(
    'Land use results',
    () => _wrap(LandUseScreen(
      bounds: _testBounds,
      service: kAnalyticsServices.firstWhere(
        (s) => s.id == 'land_use_classification',
      ),
    )),
    interact: _loadSampleResult,
  );

  _expectNoOverflowAnywhere(
    'Placeholder tab',
    () => _wrap(const PlaceholderTab(title: 'My Profile', icon: Icons.person)),
  );

  _expectNoOverflowAnywhere(
    'App shell',
    () => const UbiquitousEyeApp(),
  );
}
