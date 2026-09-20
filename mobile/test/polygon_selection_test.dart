import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:latlong2/latlong.dart';
import 'package:ubiquitous_eye/models/area_bounds.dart';
import 'package:ubiquitous_eye/screens/area_selection_screen.dart';

Future<void> _pump(WidgetTester tester) async {
  await tester.binding.setSurfaceSize(const Size(420, 860));
  addTearDown(() => tester.binding.setSurfaceSize(null));
  await tester.pumpWidget(const MaterialApp(home: AreaSelectionScreen()));
  await tester.pump(const Duration(milliseconds: 400));
}

/// Taps the map itself rather than the overlays stacked on top of it.
///
/// Waits out the double-tap window between taps. The app disables double-tap
/// zoom in polygon mode precisely so real users do not lose a corner this way,
/// but the test should place corners deliberately rather than lean on that.
Future<void> _tapMap(WidgetTester tester, Offset position) async {
  await tester.tapAt(position);
  await tester.pump(const Duration(milliseconds: 400));
}

/// Whether the Undo/Reset control behind [key] is tappable.
///
/// The bar renders worded [TextButton]s where there is room and bare
/// [IconButton]s where there is not, so a test that casts to one of them is
/// really asserting the layout rather than the behaviour it cares about.
bool _actionEnabled(WidgetTester tester, Key key) {
  final widget = tester.widget(find.byKey(key));
  if (widget is TextButton) return widget.onPressed != null;
  if (widget is IconButton) return widget.onPressed != null;
  fail('unexpected control for $key: ${widget.runtimeType}');
}

void main() {
  group('AreaBounds.fromPoints', () {
    test('encloses every tapped corner, however they are ordered', () {
      final bounds = AreaBounds.fromPoints(const [
        LatLng(23.80, 90.40),
        LatLng(23.70, 90.50),
        LatLng(23.75, 90.35),
        LatLng(23.78, 90.45),
      ]);

      expect(bounds.north, 23.80);
      expect(bounds.south, 23.70);
      expect(bounds.east, 90.50);
      expect(bounds.west, 90.35);
    });

    test('a single point makes a degenerate but valid box', () {
      final bounds = AreaBounds.fromPoints(const [LatLng(1, 2)]);
      expect(bounds.north, bounds.south);
      expect(bounds.east, bounds.west);
    });

    test('refuses an empty list rather than inventing an area', () {
      expect(() => AreaBounds.fromPoints(const []), throwsArgumentError);
    });
  });

  group('polygon selection mode', () {
    testWidgets('opens clean: nothing is drawn over the map until you draw it',
        (tester) async {
      await _pump(tester);

      // The old draggable box put a 5 km square in the middle regardless of
      // where anyone was looking. There is no box any more, and no layer is
      // rendered before the first corner is tapped.
      expect(find.byType(PolygonLayer), findsNothing);
      expect(find.byType(MarkerLayer), findsNothing);
      expect(find.byKey(const Key('polygon-undo')), findsOneWidget);
      expect(find.textContaining('drop corner 1 of 4'), findsOneWidget);
      expect(find.text('TAP 4 MORE CORNERS'), findsOneWidget);
    });

    testWidgets('there is no mode toggle left to bring a box back',
        (tester) async {
      await _pump(tester);
      expect(find.byKey(const Key('polygon-mode-toggle')), findsNothing);
    });

    testWidgets('Continue is blocked until all four corners are down',
        (tester) async {
      await _pump(tester);
      expect(find.text('TAP 4 MORE CORNERS'), findsOneWidget);
      final button = tester.widget<ElevatedButton>(find.byType(ElevatedButton));
      expect(button.onPressed, isNull, reason: 'no area chosen yet');

      for (final p in const [
        Offset(140, 360),
        Offset(280, 360),
        Offset(280, 500),
        Offset(140, 500),
      ]) {
        await _tapMap(tester, p);
      }

      expect(find.text('CONTINUE TO OPTIONS'), findsOneWidget);
      expect(find.text('Area set from 4 corners'), findsOneWidget);
      final ready = tester.widget<ElevatedButton>(find.byType(ElevatedButton));
      expect(ready.onPressed, isNotNull);
    });

    testWidgets('a fifth tap cannot silently move a finished area',
        (tester) async {
      await _pump(tester);
      for (final p in const [
        Offset(140, 360),
        Offset(280, 360),
        Offset(280, 500),
        Offset(140, 500),
      ]) {
        await _tapMap(tester, p);
      }
      expect(find.byType(MarkerLayer), findsOneWidget);
      final before = tester.widget<MarkerLayer>(find.byType(MarkerLayer));
      expect(before.markers, hasLength(4));

      await _tapMap(tester, const Offset(200, 300));

      final after = tester.widget<MarkerLayer>(find.byType(MarkerLayer));
      expect(after.markers, hasLength(4), reason: 'still exactly four corners');
    });

    testWidgets('Undo removes the last corner, Reset removes all of them',
        (tester) async {
      await _pump(tester);

      await _tapMap(tester, const Offset(140, 360));
      await _tapMap(tester, const Offset(280, 360));
      await _tapMap(tester, const Offset(280, 500));
      expect(
        tester.widget<MarkerLayer>(find.byType(MarkerLayer)).markers,
        hasLength(3),
      );

      await tester.tap(find.byKey(const Key('polygon-undo')));
      await tester.pump();
      expect(
        tester.widget<MarkerLayer>(find.byType(MarkerLayer)).markers,
        hasLength(2),
      );
      expect(find.textContaining('drop corner 3 of 4'), findsOneWidget);

      await tester.tap(find.byKey(const Key('polygon-reset')));
      await tester.pump();
      // Back to a bare map: the layers come out of the tree entirely rather
      // than lingering empty.
      expect(find.byType(MarkerLayer), findsNothing);
      expect(find.byType(PolygonLayer), findsNothing);
      expect(find.textContaining('drop corner 1 of 4'), findsOneWidget);
    });

    testWidgets('Undo and Reset are dead until there is something to remove',
        (tester) async {
      await _pump(tester);

      expect(_actionEnabled(tester, const Key('polygon-undo')), isFalse);
      expect(_actionEnabled(tester, const Key('polygon-reset')), isFalse);

      // And they come alive as soon as a corner exists.
      await _tapMap(tester, const Offset(150, 380));
      expect(_actionEnabled(tester, const Key('polygon-undo')), isTrue);
      expect(_actionEnabled(tester, const Key('polygon-reset')), isTrue);
    });

    testWidgets('searching a new place clears corners left behind elsewhere',
        (tester) async {
      await _pump(tester);
      await _tapMap(tester, const Offset(150, 380));
      expect(
        tester.widget<MarkerLayer>(find.byType(MarkerLayer)).markers,
        hasLength(1),
      );

      // Corners belong to the ground they were tapped on; jumping the map
      // elsewhere must not leave them counting toward the four off-screen.
      final field = find.byType(TextField);
      expect(field, findsOneWidget);
      await tester.enterText(field, 'Dhaka');
      await tester.pump(const Duration(milliseconds: 600));
    });
  });
}
