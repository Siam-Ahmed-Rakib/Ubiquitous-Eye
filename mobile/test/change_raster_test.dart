import 'dart:typed_data';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:latlong2/latlong.dart';
import 'package:terrascope/models/analysis_result.dart';
import 'package:terrascope/models/area_bounds.dart';
import 'package:terrascope/services/analysis_service.dart';
import 'package:terrascope/util/change_raster.dart';
import 'package:terrascope/widgets/change_mask_layer.dart';

/// A unit box sampled every 0.1° — 11 cells on each side.
const _bounds = AreaBounds(north: 1, south: 0, east: 1, west: 0);
const _pitch = 0.1;
const _side = 11;

/// A changed pixel at grid cell (col, row), counting rows from the north edge.
ChangePoint _cell(int col, int row, int mask) => ChangePoint(
      location: LatLng(_bounds.north - row * _pitch, _bounds.west + col * _pitch),
      mask: mask,
    );

/// Decoded RGBA of a rendered mask, so individual cells can be read back.
class _Raster {
  final int width;
  final int height;
  final ByteData pixels;

  const _Raster(this.width, this.height, this.pixels);

  int alphaAt(int col, int row) =>
      pixels.getUint8(((row * width) + col) * 4 + 3);

  List<int> rgbAt(int col, int row) {
    final o = ((row * width) + col) * 4;
    return [pixels.getUint8(o), pixels.getUint8(o + 1), pixels.getUint8(o + 2)];
  }
}

Future<_Raster> _decode(Uint8List png) async {
  final codec = await ui.instantiateImageCodec(png);
  final frame = await codec.getNextFrame();
  final data = await frame.image.toByteData(format: ui.ImageByteFormat.rawRgba);
  return _Raster(frame.image.width, frame.image.height, data!);
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('rasterizeChanges', () {
    test('returns null for a class with no changed pixels', () async {
      final png = await rasterizeChanges(
        changes: [_cell(3, 3, 1)],
        bounds: _bounds,
        mask: 2,
        color: kWaterLossColor,
      );
      expect(png, isNull);
    });

    test('sizes the raster from the grid the pixels sit on', () async {
      final png = await rasterizeChanges(
        changes: [_cell(0, 0, 1), _cell(1, 1, 1), _cell(10, 10, 1)],
        bounds: _bounds,
        mask: 1,
        color: kDeforestationColor,
      );
      final raster = await _decode(png!);

      expect(raster.width, _side);
      expect(raster.height, _side);
    });

    test('fills a solid patch solid, with nothing painted outside it', () async {
      // A 4×4 block of changed pixels: neighbouring points must land in
      // neighbouring cells, or the patch renders as a scatter of dots.
      final changes = <ChangePoint>[
        for (var col = 2; col < 6; col++)
          for (var row = 5; row < 9; row++) _cell(col, row, 1),
      ];

      final png = await rasterizeChanges(
        changes: changes,
        bounds: _bounds,
        mask: 1,
        color: kDeforestationColor,
      );
      final raster = await _decode(png!);

      for (var col = 2; col < 6; col++) {
        for (var row = 5; row < 9; row++) {
          expect(raster.alphaAt(col, row), 255,
              reason: 'cell ($col,$row) should be inside the patch');
        }
      }
      expect(raster.rgbAt(3, 6), [229, 57, 53]);

      // Unchanged ground stays clear, so the mask can cover the whole box.
      expect(raster.alphaAt(0, 0), 0);
      expect(raster.alphaAt(1, 6), 0);
      expect(raster.alphaAt(6, 6), 0);
      expect(raster.alphaAt(3, 9), 0);
    });

    test('paints only the class it was asked for', () async {
      final changes = [
        _cell(2, 2, 1),
        _cell(3, 2, 1),
        _cell(4, 2, 1),
        _cell(2, 3, 1),
        _cell(8, 8, 2),
      ];

      final water = await _decode((await rasterizeChanges(
        changes: changes,
        bounds: _bounds,
        mask: 2,
        color: kWaterLossColor,
      ))!);

      // A lone water pixel still gets the grid the whole result was sampled on,
      // so it stays one cell instead of swelling to fill its own raster.
      expect(water.width, _side);
      expect(water.height, _side);
      expect(water.alphaAt(8, 8), 255);
      expect(water.alphaAt(2, 2), 0);
    });

    test('keeps a single changed pixel small when no grid can be read', () async {
      final png = await rasterizeChanges(
        changes: [_cell(5, 5, 1)],
        bounds: _bounds,
        mask: 1,
        color: kDeforestationColor,
      );
      final raster = await _decode(png!);

      // Nothing to measure a pitch against, so the raster falls back to a fine
      // grid — the one pixel must not become a slab of the map.
      expect(raster.width, 513);
      expect(raster.height, 513);
    });
  });

  group('sampleAnalysis', () {
    test('emits grid-aligned pixels that a mask can be painted from', () async {
      final result = sampleAnalysis(
        bounds: _bounds,
        oldYear: 2024,
        oldMonth: 1,
        newYear: 2025,
        newMonth: 1,
      );

      expect(result.isSample, isTrue);
      expect(result.deforestationCount, greaterThan(0));
      expect(result.waterLossCount, greaterThan(0));

      // No pixel may be reported twice — the counts are areas.
      final seen = result.changes
          .map((c) => '${c.location.latitude},${c.location.longitude}')
          .toSet();
      expect(seen.length, result.changes.length);

      final png = await rasterizeChanges(
        changes: result.changes,
        bounds: _bounds,
        mask: 1,
        color: kDeforestationColor,
      );
      final raster = await _decode(png!);

      // 200 steps across the box, so the grid is one cell wider than that.
      expect(raster.width, 201);
      expect(raster.height, 201);

      final painted = [
        for (var row = 0; row < raster.height; row++)
          for (var col = 0; col < raster.width; col++)
            if (raster.alphaAt(col, row) == 255) 1,
      ].length;
      expect(painted, result.deforestationCount);
    });
  });

  group('ChangeMaskLayer', () {
    testWidgets('stretches each mask across the box without smoothing it',
        (tester) async {
      // Encoding a PNG is real engine work, so it has to happen outside the
      // widget tester's fake-async zone.
      late Uint8List png;
      await tester.runAsync(() async {
        png = (await rasterizeChanges(
          changes: [_cell(2, 2, 1), _cell(3, 2, 1), _cell(2, 3, 1)],
          bounds: _bounds,
          mask: 1,
          color: kDeforestationColor,
        ))!;
      });

      await tester.pumpWidget(MaterialApp(
        home: FlutterMap(
          options: const MapOptions(initialCenter: LatLng(0.5, 0.5), initialZoom: 9),
          children: [
            ChangeMaskLayer(
              bounds: LatLngBounds(_bounds.sw, _bounds.ne),
              masks: [
                ChangeMaskImage(image: MemoryImage(png), opacity: 0.85),
                ChangeMaskImage(image: MemoryImage(png), opacity: 0.85),
              ],
            ),
          ],
        ),
      ));

      final images = tester.widgetList<Image>(find.byType(Image)).toList();
      expect(images, hasLength(2));
      for (final image in images) {
        // A mask cell is a measurement; interpolating it would soften the very
        // edges the classifier drew.
        expect(image.filterQuality, FilterQuality.none);
        expect(image.fit, BoxFit.fill);
      }
    });

    testWidgets('draws nothing when no class produced a raster', (tester) async {
      await tester.pumpWidget(MaterialApp(
        home: FlutterMap(
          options: const MapOptions(initialCenter: LatLng(0.5, 0.5), initialZoom: 9),
          children: [
            ChangeMaskLayer(
              bounds: LatLngBounds(_bounds.sw, _bounds.ne),
              masks: const [],
            ),
          ],
        ),
      ));

      expect(find.byType(Image), findsNothing);
    });
  });
}
