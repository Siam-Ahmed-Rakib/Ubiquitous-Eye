import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:ubiquitous_eye/models/analytics_service.dart';
import 'package:ubiquitous_eye/screens/analytics/service_detail_page.dart';

final _service =
    kAnalyticsServices.firstWhere((s) => s.id == 'land_use_classification');

void main() {
  /// Regression: `_OrderBar` centred its button with a `ResponsiveCenter`, whose
  /// `Align` expands to whatever height it is offered. The Scaffold hands its
  /// `bottomNavigationBar` loose constraints, so the bar grew to the full screen
  /// and the body collapsed to zero height — silently hiding the back button,
  /// the thumbnail and the entire description.
  testWidgets('renders its body alongside the order bar', (tester) async {
    await tester.pumpWidget(
      MaterialApp(home: ServiceDetailPage(service: _service)),
    );
    await tester.pump();

    final screen = tester.getSize(find.byType(MaterialApp));
    final orderButton = tester.getRect(find.byType(ElevatedButton));
    final body = tester.getRect(find.byType(CustomScrollView));

    // The order bar hugs the bottom edge rather than filling the screen.
    expect(orderButton.bottom, closeTo(screen.height, 20));
    expect(orderButton.height, closeTo(54, 0.5));

    // The body gets everything above it.
    expect(body.height, greaterThan(screen.height * 0.7));
  });

  testWidgets('shows a back button, the title and the description',
      (tester) async {
    await tester.pumpWidget(
      MaterialApp(home: ServiceDetailPage(service: _service)),
    );
    await tester.pump();

    expect(find.byIcon(Icons.arrow_back), findsOneWidget);
    expect(find.text(_service.name), findsOneWidget);
    expect(find.text('Description'), findsOneWidget);
    expect(find.text(_service.delivery), findsOneWidget);
    expect(find.text('ORDER'), findsOneWidget);
  });

  testWidgets('the back button pops the route', (tester) async {
    final navigator = GlobalKey<NavigatorState>();
    await tester.pumpWidget(
      MaterialApp(
        navigatorKey: navigator,
        home: const Scaffold(body: Text('catalog')),
      ),
    );

    navigator.currentState!.push(
      MaterialPageRoute<void>(builder: (_) => ServiceDetailPage(service: _service)),
    );
    await tester.pumpAndSettle();
    expect(find.text('catalog'), findsNothing);

    await tester.tap(find.byIcon(Icons.arrow_back));
    await tester.pumpAndSettle();
    expect(find.text('catalog'), findsOneWidget);
  });
}
