import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:ubiquitous_eye/models/analysis_result.dart';
import 'package:ubiquitous_eye/models/area_bounds.dart';
import 'package:ubiquitous_eye/models/land_use_result.dart';
import 'package:ubiquitous_eye/services/analysis_service.dart';
import 'package:ubiquitous_eye/widgets/before_after_compare.dart';

/// Four distinct 4×3 PNGs. Real encoded images rather than a stub, because the
/// widgets under test decode them — a placeholder would fail in the rasteriser
/// rather than in the assertion.
const _scenePng =
    'iVBORw0KGgoAAAANSUhEUgAAAAQAAAADCAYAAAC09K7GAAAAFUlEQVR42mOsqKj4z4AEmBjQAIYAAHSFAm2mhm2XAAAAAElFTkSuQmCC';
const _classPng =
    'iVBORw0KGgoAAAANSUhEUgAAAAQAAAADCAYAAAC09K7GAAAAFUlEQVR42mPUrTX6z4AEmBjQAIYAAFm0AeEv1f9xAAAAAElFTkSuQmCC';
const _defoPng =
    'iVBORw0KGgoAAAANSUhEUgAAAAQAAAADCAYAAAC09K7GAAAAFUlEQVR42mN8amn6nwEJMDGgAQwBAHEwAliePUr1AAAAAElFTkSuQmCC';
const _waterPng =
    'iVBORw0KGgoAAAANSUhEUgAAAAQAAAADCAYAAAC09K7GAAAAFUlEQVR42mP83cPwnwEJMDGgAQwBAHtvAozCJ37HAAAAAElFTkSuQmCC';

const _bounds =
    AreaBounds(north: 23.789, south: 23.771, east: 90.41, west: 90.39);

Uint8List _bytes(String b64) => base64Decode(b64);

/// A backend response carrying everything the redesigned view can show.
Map<String, dynamic> _fullBody() => {
      'status': 'success',
      'message': 'Analysis complete: 3 changed pixels detected',
      'changes': [
        {'Longitude': 90.40, 'Latitude': 23.78, 'mask': 1},
        {'Longitude': 90.401, 'Latitude': 23.781, 'mask': 1},
        {'Longitude': 90.402, 'Latitude': 23.782, 'mask': 2},
      ],
      'oldImagePngBase64': _scenePng,
      'newImagePngBase64': _scenePng,
      'oldClassPngBase64': _classPng,
      'newClassPngBase64': _classPng,
      'deforestationPngBase64': _defoPng,
      'waterLossPngBase64': _waterPng,
      'imageWidth': 4,
      'imageHeight': 3,
      'bounds': {
        'north': 23.789,
        'south': 23.771,
        'east': 90.41,
        'west': 90.39,
      },
      'classBreakdown': {
        'old': [
          {'name': 'Tree', 'color': '#2D7D32', 'pixels': 700, 'percent': 70.0},
          {'name': 'Soil', 'color': '#B08968', 'pixels': 200, 'percent': 20.0},
          {'name': 'Water', 'color': '#1565C0', 'pixels': 100, 'percent': 10.0},
        ],
        'new': [
          {'name': 'Tree', 'color': '#2D7D32', 'pixels': 400, 'percent': 40.0},
          {'name': 'Soil', 'color': '#B08968', 'pixels': 500, 'percent': 50.0},
          {'name': 'Water', 'color': '#1565C0', 'pixels': 100, 'percent': 10.0},
        ],
      },
      'stats': {
        'totalPixels': 1000,
        'eligiblePixels': 998,
        'uncertainPixels': 2,
        'minimumClearObservations': 2,
        'deforestation': 2,
        'waterLoss': 1,
        'oldDate': '2024-01',
        'newDate': '2025-08',
        'oldWindow': '1–15 Jan 2024',
        'newWindow': '1–15 Aug 2025',
      },
      'requestId': 'testreq',
    };

Future<AnalysisResult> _analyze(Map<String, dynamic> body) {
  final service = AnalysisService(
    baseUrl: 'http://test.local',
    client: MockClient((_) async => http.Response(
          jsonEncode(body),
          200,
          headers: {'content-type': 'application/json'},
        )),
  );
  return service.analyze(
    bounds: _bounds,
    oldYear: 2024,
    oldMonth: 1,
    newYear: 2025,
    newMonth: 8,
  );
}

/// Hosts the widget in a window wide enough that nothing is clipped, so a
/// missing element is a real absence rather than an overflow.
Future<void> _pump(WidgetTester tester, AnalysisResult result) async {
  tester.view.physicalSize = const Size(1400, 2400);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);

  await tester.pumpWidget(MaterialApp(
    home: Scaffold(
      body: SingleChildScrollView(
        child: BeforeAfterCompare(result: result),
      ),
    ),
  ));
  await tester.pumpAndSettle();
}

/// The image a pane is currently showing. Panes key their image by whether it
/// is the class map, which is exactly the thing under test.
Finder _paneImages({required bool classified}) =>
    find.byKey(ValueKey(classified), skipOffstage: false);

void main() {
  group('AnalysisService parses the class map fields', () {
    test('decodes both class rasters and both breakdowns', () async {
      final result = await _analyze(_fullBody());

      expect(result.oldClassPng, equals(_bytes(_classPng)));
      expect(result.newClassPng, equals(_bytes(_classPng)));
      expect(result.hasClassMaps, isTrue);

      expect(result.oldClasses.map((c) => c.name), ['Tree', 'Soil', 'Water']);
      expect(result.newClasses.map((c) => c.name), ['Tree', 'Soil', 'Water']);
      // The story the two dates tell: forest down, bare soil up.
      expect(result.oldClasses.first.percent, 70.0);
      expect(result.newClasses.first.percent, 40.0);
      expect(result.stats?.eligiblePixels, 998);
      expect(result.stats?.uncertainPixels, 2);
      expect(result.stats?.minimumClearObservations, 2);
    });

    test('a response without class maps still parses', () async {
      final body = _fullBody()
        ..remove('oldClassPngBase64')
        ..remove('newClassPngBase64')
        ..remove('classBreakdown');
      final result = await _analyze(body);

      expect(result.hasClassMaps, isFalse);
      expect(result.oldClasses, isEmpty);
      expect(result.newClasses, isEmpty);
      // The scenes are unaffected — the toggle degrades, the view does not.
      expect(result.hasComparisonImagery, isTrue);
    });

    test('a cached subset (rasters, no breakdown) keeps the maps', () async {
      final body = _fullBody()..remove('classBreakdown');
      final result = await _analyze(body);

      expect(result.hasClassMaps, isTrue);
      expect(result.oldClasses, isEmpty, reason: 'shares are not recountable');
    });

    test('a malformed breakdown is ignored, not thrown on', () async {
      final body = _fullBody()..['classBreakdown'] = 'not-an-object';
      final result = await _analyze(body);

      expect(result.oldClasses, isEmpty);
      expect(result.hasClassMaps, isTrue);
    });
  });

  group('BeforeAfterCompare', () {
    testWidgets('shows before and after side by side', (tester) async {
      await _pump(tester, await _analyze(_fullBody()));

      expect(find.text('BEFORE'), findsOneWidget);
      expect(find.text('AFTER'), findsOneWidget);

      final before = tester.getCenter(find.text('BEFORE'));
      final after = tester.getCenter(find.text('AFTER'));
      expect(after.dx, greaterThan(before.dx),
          reason: 'after must sit to the right of before');
      expect((after.dy - before.dy).abs(), lessThan(1.0),
          reason: 'the two panes must share a baseline, not stack');
    });

    testWidgets('dates come from the composite window, not the month',
        (tester) async {
      await _pump(tester, await _analyze(_fullBody()));

      expect(find.text('1–15 Jan 2024'), findsOneWidget);
      expect(find.text('1–15 Aug 2025'), findsOneWidget);
    });

    testWidgets('starts on the class map and switches to raw imagery',
        (tester) async {
      await _pump(tester, await _analyze(_fullBody()));

      // On by default: both panes plus the difference base are class maps.
      expect(_paneImages(classified: true), findsNWidgets(2));
      expect(_paneImages(classified: false), findsNothing);
      expect(find.text('Coloured by class'), findsOneWidget);

      await tester.tap(find.byType(Switch));
      await tester.pumpAndSettle();

      expect(_paneImages(classified: false), findsNWidgets(2));
      expect(_paneImages(classified: true), findsNothing);
      expect(find.text('Raw imagery'), findsOneWidget);
    });

    testWidgets('the legend names all three ground types', (tester) async {
      await _pump(tester, await _analyze(_fullBody()));

      for (final name in kClassMapPalette.keys) {
        expect(find.text(name), findsWidgets, reason: '$name missing');
      }
      expect(kClassMapPalette.keys, containsAll(['Tree', 'Water', 'Soil']));
    });

    testWidgets('each pane reports its own composition', (tester) async {
      await _pump(tester, await _analyze(_fullBody()));

      // Before is 70% tree; after is 40% — the deforestation, in numbers.
      expect(find.text('Tree 70%'), findsOneWidget);
      expect(find.text('Tree 40%'), findsOneWidget);
      expect(find.text('Soil 20%'), findsOneWidget);
      expect(find.text('Soil 50%'), findsOneWidget);
    });

    testWidgets('the share bar is actually drawn, and in proportion',
        (tester) async {
      await _pump(tester, await _analyze(_fullBody()));

      // A childless ColoredBox under a Row's default (loose) cross-axis
      // constraints collapses to zero height and vanishes silently — the bar
      // is still in the tree, still the right colours, and invisible.
      final segments = tester
          .widgetList<ColoredBox>(find.descendant(
            of: find.byType(BeforeAfterCompare),
            matching: find.byType(ColoredBox),
          ))
          .where((b) => kClassMapPalette.containsValue(b.color))
          .toList();
      expect(segments, hasLength(6), reason: '3 classes on each of 2 panes');

      final sized = tester
          .renderObjectList<RenderBox>(find.byWidgetPredicate(
            (w) => w is ColoredBox && kClassMapPalette.containsValue(w.color),
          ))
          .toList();
      for (final box in sized) {
        expect(box.size.height, greaterThan(0),
            reason: 'a zero-height segment paints nothing');
        expect(box.size.width, greaterThan(0));
      }

      // Widths follow the pixel counts: before is 70/20/10, so its Tree
      // segment must be the widest and its Water the narrowest.
      final before = sized.take(3).map((b) => b.size.width).toList();
      expect(before[0], greaterThan(before[1]));
      expect(before[1], greaterThan(before[2]));
    });

    testWidgets('composition disappears with the colouring it describes',
        (tester) async {
      await _pump(tester, await _analyze(_fullBody()));
      expect(find.text('Tree 70%'), findsOneWidget);

      await tester.tap(find.byType(Switch));
      await tester.pumpAndSettle();

      expect(find.text('Tree 70%'), findsNothing);
    });

    testWidgets('the difference panel shows both change classes with counts',
        (tester) async {
      await _pump(tester, await _analyze(_fullBody()));

      expect(find.text('CHANGE'), findsOneWidget);
      expect(find.text('Deforestation · 2'), findsOneWidget);
      expect(find.text('Water loss · 1'), findsOneWidget);
      expect(
        find.text(
          '2 pixels were excluded: one or both dates had fewer than 2 clear observations.',
        ),
        findsOneWidget,
      );
    });

    testWidgets('a change class can be hidden without affecting the other',
        (tester) async {
      final result = await _analyze(_fullBody());
      await _pump(tester, result);

      Finder maskFor(Uint8List bytes) => find.byWidgetPredicate(
            (w) =>
                w is Image &&
                w.image is MemoryImage &&
                (w.image as MemoryImage).bytes == bytes,
          );

      expect(maskFor(result.deforestationPng!), findsOneWidget);
      expect(maskFor(result.waterLossPng!), findsOneWidget);

      await tester.tap(find.text('Deforestation · 2'));
      await tester.pumpAndSettle();

      expect(maskFor(result.deforestationPng!), findsNothing);
      expect(maskFor(result.waterLossPng!), findsOneWidget);
    });

    testWidgets('the switch is disabled and off without class maps',
        (tester) async {
      final body = _fullBody()
        ..remove('oldClassPngBase64')
        ..remove('newClassPngBase64');
      await _pump(tester, await _analyze(body));

      final toggle = tester.widget<Switch>(find.byType(Switch));
      expect(toggle.onChanged, isNull, reason: 'nothing to switch to');
      expect(toggle.value, isFalse);
      expect(find.text('Unavailable for this run'), findsOneWidget);
      // The raw scenes still render, so the view is not lost.
      expect(_paneImages(classified: false), findsNWidgets(2));
    });

    testWidgets('says so when the dates came back identical', (tester) async {
      final body = _fullBody()
        ..['changes'] = []
        ..['stats'] = {
          ..._fullBody()['stats'] as Map<String, dynamic>,
          'deforestation': 0,
          'waterLoss': 0,
        };
      await _pump(tester, await _analyze(body));

      expect(find.text('No confirmed change'), findsOneWidget);
      expect(find.text('Deforestation · none'), findsOneWidget);
      expect(find.text('Water loss · none'), findsOneWidget);
    });

    testWidgets('all three pictures pan and zoom together', (tester) async {
      await _pump(tester, await _analyze(_fullBody()));

      final viewers = tester
          .widgetList<InteractiveViewer>(find.byType(InteractiveViewer))
          .toList();
      expect(viewers, hasLength(3), reason: 'before, after, difference');

      final shared = viewers.first.transformationController;
      expect(shared, isNotNull);
      for (final v in viewers) {
        expect(identical(v.transformationController, shared), isTrue,
            reason: 'every pane must share one controller');
      }
    });

    testWidgets('reset zoom returns the shared transform to identity',
        (tester) async {
      await _pump(tester, await _analyze(_fullBody()));

      final controller = tester
          .widget<InteractiveViewer>(find.byType(InteractiveViewer).first)
          .transformationController!;
      controller.value = Matrix4.identity()..scaleByDouble(3, 3, 3, 1);
      await tester.pump();
      expect(controller.value, isNot(Matrix4.identity()));

      await tester.tap(find.byTooltip('Reset zoom'));
      await tester.pumpAndSettle();

      expect(controller.value, Matrix4.identity());
    });

    testWidgets('lays out on a narrow phone without overflowing',
        (tester) async {
      final result = await _analyze(_fullBody());
      tester.view.physicalSize = const Size(360, 1400);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);

      await tester.pumpWidget(MaterialApp(
        home: Scaffold(
          body: SingleChildScrollView(
            child: BeforeAfterCompare(result: result),
          ),
        ),
      ));
      await tester.pumpAndSettle();

      expect(tester.takeException(), isNull);
      // Still side by side at 360 px — the comparison is the point.
      expect(find.text('BEFORE'), findsOneWidget);
      expect(find.text('AFTER'), findsOneWidget);
      expect(
        tester.getCenter(find.text('AFTER')).dx,
        greaterThan(tester.getCenter(find.text('BEFORE')).dx),
      );
    });
  });

  group('kClassMapPalette', () {
    test('matches the mid stop of each backend ramp', () {
      // CLASS_MAP_RAMPS in server/api_server.py. If a ramp moves, the legend
      // must move with it or it stops being a key to the picture.
      expect(kClassMapPalette['Tree'], const Color(0xFF2D7D32));
      expect(kClassMapPalette['Water'], const Color(0xFF1565C0));
      expect(kClassMapPalette['Soil'], const Color(0xFFB08968));
    });

    test('reuses the shared land-cover class model', () {
      final parsed = LandCoverClass.fromJson(
        {'name': 'Tree', 'color': '#2D7D32', 'pixels': 5, 'percent': 12.5},
      );
      expect(parsed.name, 'Tree');
      expect(parsed.percent, 12.5);
      expect(parsed.areaKm2, 0, reason: 'analyze reports no per-class area');
    });
  });
}
