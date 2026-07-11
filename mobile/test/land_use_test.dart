import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:terrascope/models/area_bounds.dart';
import 'package:terrascope/models/land_use_result.dart';
import 'package:terrascope/screens/analysis/land_use_screen.dart';
import 'package:terrascope/services/land_use_service.dart';

/// A 1×1 transparent PNG — enough to prove the base64 round-trip.
const _pngBase64 =
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==';

const _pngMagic = [137, 80, 78, 71, 13, 10, 26, 10];

const _bounds = AreaBounds(north: 23.789, south: 23.771, east: 90.41, west: 90.39);

Map<String, dynamic> _successBody() => {
      'status': 'success',
      'message': 'Classified 4485 of 4485 cells',
      'imagePngBase64': _pngBase64,
      'baseImagePngBase64': _pngBase64,
      'imageWidth': 552,
      'imageHeight': 520,
      'bounds': {'north': 23.789, 'south': 23.771, 'east': 90.41, 'west': 90.39},
      'classes': [
        {'name': 'Tree', 'color': '#2E7D32', 'pixels': 3000, 'percent': 66.89, 'areaKm2': 2.71},
        {'name': 'Water', 'color': '#1565C0', 'pixels': 1485, 'percent': 33.11, 'areaKm2': 1.34},
        {'name': 'Crop', 'color': '#9CCC65', 'pixels': 0, 'percent': 0.0, 'areaKm2': 0.0},
      ],
      'stats': {
        'gridWidth': 69,
        'gridHeight': 65,
        'totalCells': 4485,
        'classifiedCells': 4485,
        'resolutionMeters': 30,
        'areaKm2': 4.0551,
        'date': '2024-01',
      },
    };

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('LandUseService.classify', () {
    test('posts the AOI ring and parses the raster, bounds and classes', () async {
      late http.Request sent;
      final service = LandUseService(
        baseUrl: 'http://backend.test/',
        client: MockClient((req) async {
          sent = req;
          return http.Response(jsonEncode(_successBody()), 200);
        }),
      );

      final result = await service.classify(bounds: _bounds, year: 2024, month: 1);

      expect(sent.url.toString(), 'http://backend.test/api/sentinel/classify');
      final body = jsonDecode(sent.body) as Map<String, dynamic>;
      expect(body['year'], 2024);
      expect(body['month'], 1);
      // A closed-by-the-backend ring of [lon, lat] pairs, clockwise from NW.
      expect(body['polygon'], [
        [90.39, 23.789],
        [90.41, 23.789],
        [90.41, 23.771],
        [90.39, 23.771],
      ]);

      expect(result.imagePng.take(8), _pngMagic);
      expect(result.baseImagePng, isNotNull);
      expect(result.baseImagePng!.take(8), _pngMagic);
      expect(result.imageWidth, 552);
      expect(result.imageHeight, 520);
      expect(result.aspectRatio, closeTo(552 / 520, 1e-9));
      expect(result.bounds.north, 23.789);
      expect(result.bounds.west, 90.39);
      expect(result.date, '2024-01');
      expect(result.resolutionMeters, 30);
      expect(result.classifiedCells, 4485);
      expect(result.isSample, isFalse);

      // Zero-pixel classes are parsed but hidden from the legend.
      expect(result.classes.length, 3);
      expect(result.presentClasses.map((c) => c.name), ['Tree', 'Water']);
      expect(result.presentClasses.first.color, const Color(0xFF2E7D32));
      expect(result.coveragePercent, closeTo(100, 0.01));
    });

    test('reports partial coverage when cells were left unclassified', () async {
      final body = _successBody();
      (body['stats'] as Map<String, dynamic>)['classifiedCells'] = 4000;

      final service = LandUseService(
        baseUrl: 'http://backend.test',
        client: MockClient((_) async => http.Response(jsonEncode(body), 200)),
      );

      final result = await service.classify(bounds: _bounds, year: 2024, month: 1);
      expect(result.coveragePercent, closeTo(89.2, 0.1));
    });

    test('surfaces the backend error message', () async {
      final service = LandUseService(
        baseUrl: 'http://backend.test',
        client: MockClient(
          (_) async => http.Response(
            jsonEncode({
              'status': 'error',
              'message': 'Sentinel Hub credentials are missing.',
            }),
            500,
          ),
        ),
      );

      expect(
        () => service.classify(bounds: _bounds, year: 2024, month: 1),
        throwsA(
          isA<LandUseException>().having(
            (e) => e.message,
            'message',
            'Sentinel Hub credentials are missing.',
          ),
        ),
      );
    });

    test('rejects a success response that carries no raster', () async {
      final body = _successBody()..remove('imagePngBase64');
      final service = LandUseService(
        baseUrl: 'http://backend.test',
        client: MockClient((_) async => http.Response(jsonEncode(body), 200)),
      );

      expect(
        () => service.classify(bounds: _bounds, year: 2024, month: 1),
        throwsA(isA<LandUseException>()),
      );
    });

    test('tolerates a backend that sends no base image', () async {
      final body = _successBody()..remove('baseImagePngBase64');
      final service = LandUseService(
        baseUrl: 'http://backend.test',
        client: MockClient((_) async => http.Response(jsonEncode(body), 200)),
      );

      final result = await service.classify(bounds: _bounds, year: 2024, month: 1);
      expect(result.baseImagePng, isNull);
      expect(result.imagePng.take(8), _pngMagic);
    });

    test('fails with a reachable message when the backend is down', () async {
      final service = LandUseService(
        baseUrl: 'http://backend.test',
        client: MockClient((_) async => throw const SocketExceptionStub()),
      );

      expect(
        () => service.classify(bounds: _bounds, year: 2024, month: 1),
        throwsA(
          isA<LandUseException>().having(
            (e) => e.message,
            'message',
            contains('Could not reach the backend'),
          ),
        ),
      );
    });
  });

  group('sampleLandUse', () {
    test('encodes a real PNG and a complete class breakdown', () async {
      final result = await sampleLandUse(bounds: _bounds, year: 2023, month: 6);

      expect(result.isSample, isTrue);
      expect(result.imagePng.take(8), _pngMagic);
      expect(result.date, '2023-06');

      // A stand-in "scene" is generated too, matching the mask's shape.
      expect(result.baseImagePng, isNotNull);
      expect(result.baseImagePng!.take(8), _pngMagic);
      expect(result.imageWidth, result.imageHeight);
      expect(result.aspectRatio, 1);
      expect(result.baseImagePng, isNot(equals(result.imagePng)));

      // Every class the palette defines is accounted for exactly once.
      expect(
        result.classes.map((c) => c.name).toSet(),
        kLandCoverPalette.keys.toSet(),
      );

      final pixels = result.classes.fold<int>(0, (sum, c) => sum + c.pixels);
      expect(pixels, result.totalCells);
      expect(result.classifiedCells, result.totalCells);

      final percent = result.classes.fold<double>(0, (sum, c) => sum + c.percent);
      expect(percent, closeTo(100, 0.001));

      final area = result.classes.fold<double>(0, (sum, c) => sum + c.areaKm2);
      expect(area, closeTo(_bounds.areaKm2, 0.001));

      // Sorted largest-first, and noise should produce more than one class.
      expect(result.presentClasses.length, greaterThan(1));
      final counts = result.classes.map((c) => c.pixels).toList();
      expect(counts, orderedEquals(counts.toList()..sort((a, b) => b - a)));
    });

    test('is deterministic for a given seed', () async {
      final a = await sampleLandUse(bounds: _bounds, year: 2023, month: 6);
      final b = await sampleLandUse(bounds: _bounds, year: 2023, month: 6);
      expect(a.imagePng, b.imagePng);
    });
  });

  group('LandUseScreen', () {
    testWidgets('stacks the mask over the Sentinel-2 scene, semi-transparently',
        (tester) async {
      await tester.pumpWidget(
        const MaterialApp(home: LandUseScreen(bounds: _bounds)),
      );

      // Before a run there is no imagery — just the prompt, picker and CTA.
      expect(find.byKey(kLandUseBaseImageKey), findsNothing);
      expect(find.byKey(kLandUseOverlayImageKey), findsNothing);
      expect(find.textContaining('Sentinel-2 scene appears here'), findsOneWidget);
      expect(find.text('RUN CLASSIFICATION'), findsOneWidget);

      await _loadSample(tester);

      expect(find.byKey(kLandUseBaseImageKey), findsOneWidget);
      expect(find.byKey(kLandUseOverlayImageKey), findsOneWidget);

      // The mask is drawn on top, faded, so the scene reads through it.
      final opacity = tester.widget<Opacity>(
        find.ancestor(
          of: find.byKey(kLandUseOverlayImageKey),
          matching: find.byType(Opacity),
        ),
      );
      expect(opacity.opacity, 0.65);

      // Both rasters fill one aspect-correct box, so they line up exactly.
      final box = tester.getRect(find.byType(AspectRatio));
      expect(tester.getRect(find.byKey(kLandUseBaseImageKey)), box);
      expect(tester.getRect(find.byKey(kLandUseOverlayImageKey)), box);

      // Sample results are labelled, and the legend names the classes found.
      expect(find.textContaining('Sample data'), findsOneWidget);
      expect(find.text('Land cover'), findsOneWidget);
      expect(find.text('Tree'), findsWidgets);
    });

    testWidgets('hiding the mask leaves the raw scene visible', (tester) async {
      await tester.pumpWidget(
        const MaterialApp(home: LandUseScreen(bounds: _bounds)),
      );

      await _loadSample(tester);
      expect(find.byKey(kLandUseOverlayImageKey), findsOneWidget);

      final hide = find.byIcon(Icons.visibility_outlined);
      await tester.ensureVisible(hide);
      await tester.tap(hide);
      await tester.pump();

      expect(find.byKey(kLandUseOverlayImageKey), findsNothing);
      expect(find.byKey(kLandUseBaseImageKey), findsOneWidget);
    });

    testWidgets('on a wide window the panel becomes a right sidebar',
        (tester) async {
      tester.view.physicalSize = const Size(1200, 800);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      await tester.pumpWidget(
        const MaterialApp(home: LandUseScreen(bounds: _bounds)),
      );
      await _loadSample(tester);

      expect(find.byKey(kLandUseSidePanelKey), findsOneWidget);

      // The panel sits to the right of the scene, not below it.
      final panel = tester.getRect(find.byKey(kLandUseSidePanelKey));
      final viewer = tester.getRect(find.byType(InteractiveViewer));
      expect(panel.left, greaterThanOrEqualTo(viewer.right - 1));
      expect(find.text('Land cover'), findsOneWidget);
    });

    testWidgets('on a narrow window the panel stays a bottom sheet',
        (tester) async {
      tester.view.physicalSize = const Size(600, 900);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      await tester.pumpWidget(
        const MaterialApp(home: LandUseScreen(bounds: _bounds)),
      );
      await _loadSample(tester);

      expect(find.byKey(kLandUseSidePanelKey), findsNothing);

      // The panel is below the scene.
      final scene = tester.getRect(find.byType(InteractiveViewer));
      final landCover = tester.getRect(find.text('Land cover'));
      expect(landCover.top, greaterThanOrEqualTo(scene.bottom - 1));
    });

    testWidgets('the scene is pannable and zoomable', (tester) async {
      await tester.pumpWidget(
        const MaterialApp(home: LandUseScreen(bounds: _bounds)),
      );
      await _loadSample(tester);

      expect(find.byType(InteractiveViewer), findsOneWidget);

      await tester.tap(find.byTooltip('Zoom in'));
      await tester.pump();
      final zoomed = tester
          .widget<InteractiveViewer>(find.byType(InteractiveViewer))
          .transformationController!
          .value
          .getMaxScaleOnAxis();
      expect(zoomed, greaterThan(1.0));

      await tester.tap(find.byTooltip('Fit to view'));
      await tester.pump();
      final reset = tester
          .widget<InteractiveViewer>(find.byType(InteractiveViewer))
          .transformationController!
          .value
          .getMaxScaleOnAxis();
      expect(reset, 1.0);
    });
  });
}

/// Taps "Load sample result" and lets it finish.
///
/// The tap has to happen inside [WidgetTester.runAsync], not just the wait:
/// generating the sample rasters encodes PNGs through `dart:ui`, which schedules
/// engine work on whichever zone called it, and `testWidgets`' fake-async zone
/// never runs it.
Future<void> _loadSample(WidgetTester tester) async {
  final button = find.text('Load sample result (no backend needed)');
  await tester.ensureVisible(button);
  await tester.pump();

  await tester.runAsync(() async {
    await tester.tap(button);
    await Future<void>.delayed(const Duration(milliseconds: 500));
  });

  await tester.pump(); // rebuild with the decoded result
}

/// Stands in for a socket failure without importing `dart:io` (web-safe).
class SocketExceptionStub implements Exception {
  const SocketExceptionStub();
}
