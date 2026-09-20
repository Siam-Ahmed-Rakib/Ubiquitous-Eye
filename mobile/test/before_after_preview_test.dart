@Tags(['preview'])
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:ubiquitous_eye/models/analysis_result.dart';
import 'package:ubiquitous_eye/models/land_use_result.dart';
import 'package:ubiquitous_eye/widgets/before_after_compare.dart';

import 'fixtures/analysis_fixture.dart';

/// Renders the real widget over the real backend rasters and writes a PNG, so
/// the design can be looked at rather than only asserted about.
///
/// This is a *preview*, not a golden: nothing is compared, so it can never fail
/// on an unrelated rendering difference. Excluded from the default run by its
/// tag — see `dart_test.yaml`.
///
///   flutter test --run-skipped --update-goldens test/before_after_preview_test.dart
///
/// Output lands in `build/preview/`.
void main() {
  setUpAll(() async {
    // Without a real font the engine falls back to Ahem, which paints every
    // glyph as a filled box — legible layout, illegible text.
    for (final family in const {
      'Roboto': [
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
      ],
    }.entries) {
      final loader = FontLoader(family.key);
      var loaded = false;
      for (final path in family.value) {
        final file = File(path);
        if (!file.existsSync()) continue;
        loader.addFont(
          file.readAsBytes().then((b) => ByteData.view(Uint8List.fromList(b).buffer)),
        );
        loaded = true;
        break;
      }
      if (loaded) await loader.load();
    }
  });

  AnalysisResult fixture({
    bool withClassMaps = true,
    bool withChanges = true,
  }) {
    return AnalysisResult(
      message: 'Analysis complete',
      changes: const [],
      stats: AnalysisStats(
        totalPixels: 30000,
        deforestation: withChanges ? kDeforestationCount : 0,
        waterLoss: withChanges ? kWaterLossCount : 0,
        urbanization: 0,
        oldDate: '2024-01',
        newDate: '2025-08',
        oldWindow: '1–15 Jan 2024',
        newWindow: '1–15 Aug 2025',
      ),
      oldImagePng: base64Decode(kOldScenePng),
      newImagePng: base64Decode(kNewScenePng),
      oldClassPng: withClassMaps ? base64Decode(kOldClassPng) : null,
      newClassPng: withClassMaps ? base64Decode(kNewClassPng) : null,
      oldClasses: withClassMaps
          ? kOldClasses.map(LandCoverClass.fromJson).toList()
          : const [],
      newClasses: withClassMaps
          ? kNewClasses.map(LandCoverClass.fromJson).toList()
          : const [],
      deforestationPng: withChanges ? base64Decode(kDeforestationPng) : null,
      waterLossPng: withChanges ? base64Decode(kWaterLossPng) : null,
      imageWidth: kFixtureWidth,
      imageHeight: kFixtureHeight,
    );
  }

  Future<void> shoot(
    WidgetTester tester,
    String name,
    Size size,
    AnalysisResult result, {
    bool tapSwitch = false,
  }) async {
    tester.view.physicalSize = size;
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    await tester.pumpWidget(MaterialApp(
      debugShowCheckedModeBanner: false,
      theme: ThemeData(fontFamily: 'Roboto', useMaterial3: true),
      home: Scaffold(
        backgroundColor: const Color(0xFFF3F4F6),
        body: SingleChildScrollView(
          // The capture targets this box rather than the widget itself: the
          // widget paints no background of its own, so on its own it captures
          // against transparency, which encodes as black and makes every dark
          // label unreadable in the very image meant to judge them.
          child: ColoredBox(
            key: const ValueKey('preview-frame'),
            color: const Color(0xFFF3F4F6),
            child: Padding(
              padding: const EdgeInsets.all(20),
              child: BeforeAfterCompare(result: result),
            ),
          ),
        ),
      ),
    ));
    // A widget test never completes an image decode on its own — decoding is
    // real async work, which only runs inside `runAsync`, and `pumpAndSettle`
    // does not wait for it. Warming every raster into the image cache first
    // means the next paint has them synchronously and the capture shows the
    // pictures rather than five empty frames.
    await tester.runAsync(() async {
      for (final bytes in <Uint8List?>[
        result.oldImagePng,
        result.newImagePng,
        result.oldClassPng,
        result.newClassPng,
        result.deforestationPng,
        result.waterLossPng,
      ]) {
        if (bytes == null) continue;
        final stream = MemoryImage(bytes).resolve(ImageConfiguration.empty);
        final done = Completer<void>();
        late ImageStreamListener listener;
        listener = ImageStreamListener(
          (_, __) {
            stream.removeListener(listener);
            if (!done.isCompleted) done.complete();
          },
          onError: (_, __) {
            stream.removeListener(listener);
            if (!done.isCompleted) done.complete();
          },
        );
        stream.addListener(listener);
        await done.future;
      }
    });
    await tester.pumpAndSettle();

    if (tapSwitch) {
      await tester.tap(find.byType(Switch));
      await tester.pumpAndSettle();
    }

    await expectLater(
      find.byKey(const ValueKey('preview-frame')),
      matchesGoldenFile('../build/preview/$name.png'),
    );
  }

  testWidgets('wide · land cover on', (t) async {
    await shoot(t, 'wide_classified', const Size(1160, 1700), fixture());
  });

  testWidgets('wide · land cover off', (t) async {
    await shoot(t, 'wide_raw', const Size(1160, 1700), fixture(),
        tapSwitch: true);
  });

  testWidgets('phone · land cover on', (t) async {
    await shoot(t, 'phone_classified', const Size(400, 1500), fixture());
  });

  testWidgets('no class maps available', (t) async {
    await shoot(t, 'wide_no_class_maps', const Size(1160, 1700),
        fixture(withClassMaps: false));
  });

  testWidgets('no change detected', (t) async {
    await shoot(t, 'wide_no_change', const Size(1160, 1700),
        fixture(withChanges: false));
  });
}
