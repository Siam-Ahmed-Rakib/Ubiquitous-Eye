import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:latlong2/latlong.dart';
import 'package:ubiquitous_eye/models/area_bounds.dart';
import 'package:ubiquitous_eye/widgets/map_gestures.dart';
import 'package:ubiquitous_eye/widgets/selection_overlay.dart';

/// Regression tests for the selection box being undraggable on touch devices.
///
/// The box worked with a mouse and not with a finger, on both Android and
/// mobile web. The cause was the gesture arena, not the maths: flutter_map puts
/// a Horizontal- and a VerticalDragGestureRecognizer on a GestureArenaTeam, and
/// those accept at *hit* slop (18 logical px along one axis, nearer 8 on
/// Android), while `GestureDetector.onPan*` accepts at *pan* slop, twice that.
/// A straight finger drag crossed the map's threshold first every time, so the
/// map won and the box never moved. A mouse collapses both thresholds to 1-2 px
/// and the deeper widget accepts first, which is why desktop never showed it.
///
/// These drive the real arena through `WidgetTester.drag`, so they fail against
/// the old code and pass against the new.
void main() {
  Future<_HarnessState> pumpHarness(
    WidgetTester tester,
    MapController controller, {
    bool gesturesEnabled = true,
  }) async {
    await tester.pumpWidget(
      _Harness(controller: controller, gesturesEnabled: gesturesEnabled),
    );
    await tester.pumpAndSettle();
    return tester.state<_HarnessState>(find.byType(_Harness));
  }

  /// A point inside the box but clear of the centre move handle, so this
  /// exercises the "drag anywhere in the box" path a user actually uses.
  Offset interiorPoint(WidgetTester tester) =>
      tester.getCenter(find.byType(FlutterMap)) + const Offset(0, 60);

  testWidgets('a touch drag inside the box moves the box, not the map',
      (tester) async {
    final controller = MapController();
    final state = await pumpHarness(tester, controller);

    final boxBefore = state.bounds;
    final mapBefore = controller.camera.center;

    await tester.dragFrom(
      interiorPoint(tester),
      const Offset(80, 0),
      kind: PointerDeviceKind.touch,
    );
    await tester.pumpAndSettle();

    expect(
      state.bounds.west,
      greaterThan(boxBefore.west),
      reason: 'dragging right should have moved the box east',
    );
    expect(
      controller.camera.center.longitude,
      closeTo(mapBefore.longitude, 1e-9),
      reason: 'the map must not pan while the box is being dragged',
    );
  });

  testWidgets('a mouse drag still moves the box (desktop must not regress)',
      (tester) async {
    final controller = MapController();
    final state = await pumpHarness(tester, controller);

    final boxBefore = state.bounds;

    await tester.dragFrom(
      interiorPoint(tester),
      const Offset(80, 0),
      kind: PointerDeviceKind.mouse,
    );
    await tester.pumpAndSettle();

    expect(state.bounds.west, greaterThan(boxBefore.west));
  });

  testWidgets('the box tracks the finger 1:1', (tester) async {
    // Driven with explicit small moves rather than `tester.drag`, for two
    // reasons: it is what a real finger produces (a stream of small events),
    // and `tester.drag` synthesises one large slop move that the recogniser
    // discards under DragStartBehavior.start, which would make the box look
    // like it lagged by tens of pixels when it does not.
    final controller = MapController();
    final state = await pumpHarness(tester, controller);

    final startPx = controller.camera.latLngToScreenPoint(state.bounds.center);

    final gesture = await tester.startGesture(
      interiorPoint(tester),
      kind: PointerDeviceKind.touch,
    );
    for (var i = 0; i < 8; i++) {
      await gesture.moveBy(const Offset(10, -5));
      await tester.pump();
    }
    await gesture.up();
    await tester.pumpAndSettle();

    final endPx = controller.camera.latLngToScreenPoint(state.bounds.center);

    // 80 px right and 40 px up, less the few px of pan slop consumed before the
    // recogniser accepted. Pan slop here is 4 px (touchSlop 2), so the box can
    // trail the finger by at most one 10 px step.
    expect(endPx.x - startPx.x, closeTo(80, 3));
    expect(endPx.y - startPx.y, closeTo(-40, 3));
    expect(
      endPx.x - startPx.x,
      greaterThan(75),
      reason: 'more than one step lost means the old 36 px pan slop is back',
    );
  });

  testWidgets('gesturesEnabled:false hands the drag back to the map',
      (tester) async {
    // This is the pinch escape hatch. The screen sets it while two fingers are
    // down so a pinch that starts inside the box zooms the map instead of
    // dragging the box. Dropping the handles from the tree disposes their
    // recognisers, which is what actually releases the gesture.
    final controller = MapController();
    final state = await pumpHarness(tester, controller, gesturesEnabled: false);

    final boxBefore = state.bounds;
    final mapBefore = controller.camera.center;

    await tester.dragFrom(
      interiorPoint(tester),
      const Offset(80, 0),
      kind: PointerDeviceKind.touch,
    );
    await tester.pumpAndSettle();

    expect(
      state.bounds.west,
      closeTo(boxBefore.west, 1e-9),
      reason: 'the box must stay put when its gestures are disabled',
    );
    expect(
      controller.camera.center.longitude,
      isNot(closeTo(mapBefore.longitude, 1e-9)),
      reason: 'the map should have panned instead',
    );
  });

  testWidgets('a locked box lets the map pan straight through it',
      (tester) async {
    final controller = MapController();
    await tester.pumpWidget(
      _Harness(controller: controller, gesturesEnabled: true, locked: true),
    );
    await tester.pumpAndSettle();
    final state = tester.state<_HarnessState>(find.byType(_Harness));

    final boxBefore = state.bounds;
    final mapBefore = controller.camera.center;

    await tester.dragFrom(
      interiorPoint(tester),
      const Offset(80, 0),
      kind: PointerDeviceKind.touch,
    );
    await tester.pumpAndSettle();

    expect(state.bounds.west, closeTo(boxBefore.west, 1e-9));
    expect(
      controller.camera.center.longitude,
      isNot(closeTo(mapBefore.longitude, 1e-9)),
    );
  });
}

class _Harness extends StatefulWidget {
  final MapController controller;
  final bool gesturesEnabled;
  final bool locked;

  const _Harness({
    required this.controller,
    this.gesturesEnabled = true,
    this.locked = false,
  });

  @override
  State<_Harness> createState() => _HarnessState();
}

class _HarnessState extends State<_Harness> {
  static const _center = LatLng(23.7806, 90.3998);

  AreaBounds bounds = AreaBounds.square(center: _center, sizeKm: 5);
  bool dragging = false;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      home: Scaffold(
        body: FlutterMap(
          mapController: widget.controller,
          options: MapOptions(
            initialCenter: _center,
            initialZoom: 13,
            // Mirrors the real screen: map gestures are frozen for the duration
            // of a handle drag.
            interactionOptions: InteractionOptions(
              flags: dragging ? InteractiveFlag.none : kSmoothInteractiveFlags,
            ),
          ),
          children: [
            AreaSelectionOverlay(
              bounds: bounds,
              locked: widget.locked,
              gesturesEnabled: widget.gesturesEnabled,
              onChanged: (b) => setState(() => bounds = b),
              onDragStart: () => setState(() => dragging = true),
              onDragEnd: () => setState(() => dragging = false),
            ),
          ],
        ),
      ),
    );
  }
}
