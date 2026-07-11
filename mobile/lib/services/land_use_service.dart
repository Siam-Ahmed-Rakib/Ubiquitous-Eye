import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';
import 'dart:ui' as ui;

import 'package:http/http.dart' as http;

import '../config.dart';
import '../models/area_bounds.dart';
import '../models/land_use_result.dart';

/// Raised when the backend is unreachable or returns an error response.
class LandUseException implements Exception {
  final String message;
  const LandUseException(this.message);

  @override
  String toString() => message;
}

/// Client for the backend land-cover classifier.
///
/// Wraps `POST {BACKEND_URL}/api/sentinel/classify`. Given an area and a single
/// month, the backend builds a Sentinel-2 composite, labels every cell
/// Tree / Crop / Water / Soil, and returns the labels as a colour-mapped RGBA
/// PNG covering the area's bounding box.
class LandUseService {
  LandUseService({http.Client? client, String? baseUrl})
      : _client = client ?? http.Client(),
        _baseUrl = (baseUrl ?? kBackendBaseUrl).replaceAll(RegExp(r'/+$'), '');

  final http.Client _client;
  final String _baseUrl;

  String get baseUrl => _baseUrl;

  /// Classifies land cover across [bounds] for the given composite month.
  ///
  /// Throws [LandUseException] with a human-readable message on any network,
  /// HTTP, or backend-reported error (e.g. missing Sentinel Hub credentials).
  Future<LandUseResult> classify({
    required AreaBounds bounds,
    required int year,
    required int month,
  }) async {
    final uri = Uri.parse('$_baseUrl/api/sentinel/classify');

    // Backend expects a ring of [lon, lat] pairs (it closes the ring itself).
    final polygon = <List<double>>[
      [bounds.west, bounds.north],
      [bounds.east, bounds.north],
      [bounds.east, bounds.south],
      [bounds.west, bounds.south],
    ];

    http.Response res;
    try {
      res = await _client
          .post(
            uri,
            headers: const {'Content-Type': 'application/json'},
            body: jsonEncode({'polygon': polygon, 'year': year, 'month': month}),
          )
          .timeout(const Duration(minutes: 5));
    } catch (e) {
      throw LandUseException(
        'Could not reach the backend at $_baseUrl.\n'
        'Is the Flask server running? ($e)',
      );
    }

    Map<String, dynamic> body;
    try {
      body = jsonDecode(res.body) as Map<String, dynamic>;
    } catch (_) {
      throw LandUseException(
        'Backend returned HTTP ${res.statusCode} with an unexpected '
        '(non-JSON) response.',
      );
    }

    if (res.statusCode != 200 || body['status'] != 'success') {
      throw LandUseException(
        (body['message'] ?? 'Backend error (HTTP ${res.statusCode}).').toString(),
      );
    }

    final encoded = body['imagePngBase64'] as String?;
    if (encoded == null || encoded.isEmpty) {
      throw const LandUseException('Backend returned no classification raster.');
    }

    final raw = body['bounds'] as Map<String, dynamic>? ?? const {};
    final stats = body['stats'] as Map<String, dynamic>? ?? const {};

    final classes = (body['classes'] as List<dynamic>? ?? const [])
        .whereType<Map<String, dynamic>>()
        .map(LandCoverClass.fromJson)
        .toList();

    // Older backends only sent the mask; the viewer falls back to a bare
    // backdrop rather than failing the whole run.
    final baseEncoded = body['baseImagePngBase64'] as String?;

    return LandUseResult(
      imagePng: base64Decode(encoded),
      baseImagePng: (baseEncoded == null || baseEncoded.isEmpty)
          ? null
          : base64Decode(baseEncoded),
      imageWidth: (body['imageWidth'] as num?)?.toInt() ?? 0,
      imageHeight: (body['imageHeight'] as num?)?.toInt() ?? 0,
      bounds: AreaBounds(
        north: (raw['north'] as num?)?.toDouble() ?? bounds.north,
        south: (raw['south'] as num?)?.toDouble() ?? bounds.south,
        east: (raw['east'] as num?)?.toDouble() ?? bounds.east,
        west: (raw['west'] as num?)?.toDouble() ?? bounds.west,
      ),
      classes: classes,
      date: stats['date']?.toString() ??
          '$year-${month.toString().padLeft(2, '0')}',
      resolutionMeters: (stats['resolutionMeters'] as num?)?.toInt() ?? 30,
      totalCells: (stats['totalCells'] as num?)?.toInt() ?? 0,
      classifiedCells: (stats['classifiedCells'] as num?)?.toInt() ?? 0,
      areaKm2: (stats['areaKm2'] as num?)?.toDouble() ?? bounds.areaKm2,
    );
  }

  void dispose() => _client.close();
}

// ── Sample data ─────────────────────────────────────────────────────────────

const int _sampleGrid = 192;

/// Roughly what each class looks like in a true-colour scene, so the sample
/// backdrop reads as imagery rather than as a second copy of the legend.
const Map<String, List<int>> _sampleSceneColors = {
  'Water': [26, 58, 92],
  'Soil': [142, 122, 96],
  'Crop': [118, 138, 68],
  'Tree': [28, 64, 34],
};

/// Builds a synthetic, clearly-labelled classification so the overlay can be
/// seen without a running backend or Sentinel Hub credentials.
///
/// Two octaves of value noise are thresholded into the land-cover classes,
/// which yields contiguous regions rather than confetti. A matching fake
/// "scene" stands in for the Sentinel-2 backdrop. This is **not** real satellite
/// analysis — the UI marks any result with `isSample == true`.
Future<LandUseResult> sampleLandUse({
  required AreaBounds bounds,
  required int year,
  required int month,
  int seed = 42,
}) async {
  // Ordered by the noise band each class occupies, low to high: water sits in
  // the hollows, tree cover on the high ground.
  const bands = <String>['Water', 'Soil', 'Crop', 'Tree'];
  const thresholds = <double>[0.34, 0.48, 0.66];

  final mask = Uint8List(_sampleGrid * _sampleGrid * 4);
  final scene = Uint8List(_sampleGrid * _sampleGrid * 4);
  final counts = {for (final name in bands) name: 0};

  for (var y = 0; y < _sampleGrid; y++) {
    for (var x = 0; x < _sampleGrid; x++) {
      final fx = x / _sampleGrid;
      final fy = y / _sampleGrid;
      final n = 0.65 * _valueNoise(fx * 6, fy * 6, seed) +
          0.35 * _valueNoise(fx * 14, fy * 14, seed + 1);

      var band = thresholds.length;
      for (var i = 0; i < thresholds.length; i++) {
        if (n < thresholds[i]) {
          band = i;
          break;
        }
      }

      final name = bands[band];
      counts[name] = counts[name]! + 1;
      final o = (y * _sampleGrid + x) * 4;

      final color = kLandCoverPalette[name]!;
      mask[o] = (color.r * 255).round();
      mask[o + 1] = (color.g * 255).round();
      mask[o + 2] = (color.b * 255).round();
      mask[o + 3] = 255;

      // High-frequency jitter gives the backdrop a bit of sensor-like texture.
      final grain = (_valueNoise(fx * 48, fy * 48, seed + 2) - 0.5) * 44;
      final tone = _sampleSceneColors[name]!;
      scene[o] = (tone[0] + grain).clamp(0, 255).round();
      scene[o + 1] = (tone[1] + grain).clamp(0, 255).round();
      scene[o + 2] = (tone[2] + grain).clamp(0, 255).round();
      scene[o + 3] = 255;
    }
  }

  const total = _sampleGrid * _sampleGrid;
  final area = bounds.areaKm2;
  final classes = [
    for (final name in bands)
      LandCoverClass(
        name: name,
        color: kLandCoverPalette[name]!,
        pixels: counts[name]!,
        percent: 100.0 * counts[name]! / total,
        areaKm2: area * counts[name]! / total,
      ),
  ]..sort((a, b) => b.pixels.compareTo(a.pixels));

  return LandUseResult(
    imagePng: await _encodePng(mask, _sampleGrid, _sampleGrid),
    baseImagePng: await _encodePng(scene, _sampleGrid, _sampleGrid),
    imageWidth: _sampleGrid,
    imageHeight: _sampleGrid,
    bounds: bounds,
    classes: classes,
    date: '$year-${month.toString().padLeft(2, '0')}',
    resolutionMeters: 30,
    totalCells: total,
    classifiedCells: total,
    areaKm2: area,
    isSample: true,
  );
}

Future<Uint8List> _encodePng(Uint8List rgba, int width, int height) async {
  final completer = Completer<ui.Image>();
  ui.decodeImageFromPixels(
    rgba,
    width,
    height,
    ui.PixelFormat.rgba8888,
    completer.complete,
  );
  final image = await completer.future;
  final data = await image.toByteData(format: ui.ImageByteFormat.png);
  image.dispose();
  if (data == null) {
    throw const LandUseException('Could not encode the sample raster.');
  }
  return data.buffer.asUint8List();
}

/// Deterministic hash of a lattice point into [0, 1).
double _hash(int x, int y, int seed) {
  var h = x * 374761393 + y * 668265263 + seed * 2246822519;
  h = (h ^ (h >> 13)) * 1274126177;
  h ^= h >> 16;
  return (h & 0x7FFFFFFF) / 0x7FFFFFFF;
}

double _lerp(double a, double b, double t) => a + (b - a) * t;

/// Smoothed value noise sampled at ([x], [y]) on an integer lattice.
double _valueNoise(double x, double y, int seed) {
  final x0 = x.floor();
  final y0 = y.floor();
  final tx = x - x0;
  final ty = y - y0;

  // Smoothstep keeps the region borders soft rather than diamond-shaped.
  final sx = tx * tx * (3 - 2 * tx);
  final sy = ty * ty * (3 - 2 * ty);

  final top = _lerp(_hash(x0, y0, seed), _hash(x0 + 1, y0, seed), sx);
  final bottom = _lerp(_hash(x0, y0 + 1, seed), _hash(x0 + 1, y0 + 1, seed), sx);
  return _lerp(top, bottom, sy);
}
