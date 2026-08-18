import 'dart:typed_data';
import 'dart:ui' as ui;

import '../models/analysis_result.dart';
import '../models/area_bounds.dart';

/// Longest side, in cells, of a mask painted on this side. Caps the work and
/// the memory a pathological point set can ask for.
const int kChangeRasterMaxSide = 2048;

/// Cells per side when the pixel grid can't be read off the data — a result
/// with a single changed pixel, say. Only the cell size is a guess; erring fine
/// keeps that pixel a speck rather than inflating it into a slab of the map.
const int _fallbackCellsPerSide = 512;

/// Paints one change class into a transparent raster spanning [bounds].
///
/// This is the fallback for a response that carried no mask PNG — an older
/// backend, or `sampleAnalysis`. The current backend renders both classes
/// itself, and those rasters are used as-is.
///
/// Cell size comes from the spacing the points themselves sit on, so
/// neighbouring changed pixels land in neighbouring cells and meet edge to
/// edge. A cleared patch therefore fills solid instead of breaking into a
/// scatter of dots, and its border stays exactly as ragged as the classifier
/// drew it — nothing is smoothed, hulled, or squared off into a box.
///
/// The spacing is read from *every* changed pixel, not just [mask]'s: both
/// classes were sampled off one grid, and a class holding a single pixel has no
/// spacing of its own to measure.
///
/// Returns PNG bytes, or null when the class has no pixels.
Future<Uint8List?> rasterizeChanges({
  required List<ChangePoint> changes,
  required AreaBounds bounds,
  required int mask,
  required ui.Color color,
}) async {
  final points = changes.where((c) => c.mask == mask).toList(growable: false);
  if (points.isEmpty) return null;

  final lonSpan = _nonZero(bounds.east - bounds.west);
  final latSpan = _nonZero(bounds.north - bounds.south);

  final lonPitch = _pitch(changes.map((p) => p.location.longitude)) ??
      lonSpan / _fallbackCellsPerSide;
  final latPitch = _pitch(changes.map((p) => p.location.latitude)) ??
      latSpan / _fallbackCellsPerSide;

  final width = _cells(lonSpan / lonPitch + 1);
  final height = _cells(latSpan / latPitch + 1);

  final red = (color.r * 255).round();
  final green = (color.g * 255).round();
  final blue = (color.b * 255).round();

  final pixels = Uint8List(width * height * 4);
  for (final p in points) {
    final col =
        (((p.location.longitude - bounds.west) / lonSpan) * (width - 1)).round();
    final row =
        (((bounds.north - p.location.latitude) / latSpan) * (height - 1)).round();
    if (col < 0 || col >= width || row < 0 || row >= height) continue;
    final o = (row * width + col) * 4;
    pixels[o] = red;
    pixels[o + 1] = green;
    pixels[o + 2] = blue;
    pixels[o + 3] = 255;
  }

  final buffer = await ui.ImmutableBuffer.fromUint8List(pixels);
  final descriptor = ui.ImageDescriptor.raw(
    buffer,
    width: width,
    height: height,
    pixelFormat: ui.PixelFormat.rgba8888,
  );
  final codec = await descriptor.instantiateCodec();
  final frame = await codec.getNextFrame();
  final png = await frame.image.toByteData(format: ui.ImageByteFormat.png);

  frame.image.dispose();
  codec.dispose();
  descriptor.dispose();

  return png?.buffer.asUint8List();
}

/// Smallest positive gap between consecutive distinct values — the grid pitch
/// the pixels were sampled on. Null when every point shares one coordinate.
double? _pitch(Iterable<double> values) {
  final sorted = values.toSet().toList()..sort();
  var pitch = double.infinity;
  for (var i = 1; i < sorted.length; i++) {
    final gap = sorted[i] - sorted[i - 1];
    if (gap > 1e-9 && gap < pitch) pitch = gap;
  }
  return pitch.isFinite ? pitch : null;
}

double _nonZero(double v) => v.abs() < 1e-9 ? 1e-9 : v;

int _cells(double v) {
  if (!v.isFinite) return kChangeRasterMaxSide;
  final n = v.round();
  if (n < 1) return 1;
  return n > kChangeRasterMaxSide ? kChangeRasterMaxSide : n;
}
