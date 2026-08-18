import 'dart:convert';
import 'dart:math' as math;
import 'dart:typed_data';

import 'package:http/http.dart' as http;
import 'package:latlong2/latlong.dart';

import '../config.dart';
import '../models/analysis_result.dart';
import '../models/area_bounds.dart';
import '../models/land_use_result.dart';

/// Raised when the backend is unreachable or returns an error response.
class AnalysisException implements Exception {
  final String message;
  const AnalysisException(this.message);

  @override
  String toString() => message;
}

/// Client for the backend change-detection pipeline.
///
/// Wraps `POST {BACKEND_URL}/api/sentinel/analyze`, the same endpoint the React
/// web frontend uses. Given an area of interest and two dates, the backend
/// builds bimonthly Sentinel-2 composites, classifies them, and returns the
/// pixels that changed between the two dates.
class AnalysisService {
  AnalysisService({http.Client? client, String? baseUrl})
      : _client = client ?? http.Client(),
        _baseUrl = (baseUrl ?? kBackendBaseUrl).replaceAll(RegExp(r'/+$'), '');

  final http.Client _client;
  final String _baseUrl;

  String get baseUrl => _baseUrl;

  /// Runs old-vs-new change detection over [bounds].
  ///
  /// Throws [AnalysisException] with a human-readable message on any network,
  /// HTTP, or backend-reported error (e.g. missing Sentinel Hub credentials).
  Future<AnalysisResult> analyze({
    required AreaBounds bounds,
    required int oldYear,
    required int oldMonth,
    required int newYear,
    required int newMonth,
  }) async {
    final uri = Uri.parse('$_baseUrl/api/sentinel/analyze');

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
            body: jsonEncode({
              'polygon': polygon,
              'oldYear': oldYear,
              'oldMonth': oldMonth,
              'newYear': newYear,
              'newMonth': newMonth,
            }),
          )
          .timeout(const Duration(minutes: 5));
    } catch (e) {
      throw AnalysisException(
        'Could not reach the backend at $_baseUrl.\n'
        'Is the Flask server running? ($e)',
      );
    }

    Map<String, dynamic> body;
    try {
      body = jsonDecode(res.body) as Map<String, dynamic>;
    } catch (_) {
      throw AnalysisException(
        'Backend returned HTTP ${res.statusCode} with an unexpected '
        '(non-JSON) response.',
      );
    }

    if (res.statusCode != 200 || body['status'] != 'success') {
      throw AnalysisException(
        (body['message'] ?? 'Backend error (HTTP ${res.statusCode}).')
            .toString(),
      );
    }

    final rawChanges = body['changes'] as List<dynamic>? ?? const [];
    final changes = rawChanges
        .whereType<Map<String, dynamic>>()
        .map(
          (m) => ChangePoint(
            location: LatLng(
              (m['Latitude'] as num).toDouble(),
              (m['Longitude'] as num).toDouble(),
            ),
            mask: (m['mask'] as num).toInt(),
          ),
        )
        .toList();

    final stats = body['stats'] is Map<String, dynamic>
        ? AnalysisStats.fromJson(body['stats'] as Map<String, dynamic>)
        : null;

    final breakdown = body['classBreakdown'];

    return AnalysisResult(
      message: body['message']?.toString() ?? 'Analysis complete',
      changes: changes,
      stats: stats,
      oldImagePng: _decodePng(body['oldImagePngBase64']),
      newImagePng: _decodePng(body['newImagePngBase64']),
      oldClassPng: _decodePng(body['oldClassPngBase64']),
      newClassPng: _decodePng(body['newClassPngBase64']),
      oldClasses: _parseClasses(breakdown, 'old'),
      newClasses: _parseClasses(breakdown, 'new'),
      deforestationPng: _decodePng(body['deforestationPngBase64']),
      waterLossPng: _decodePng(body['waterLossPngBase64']),
      imageWidth: (body['imageWidth'] as num?)?.toInt() ?? 0,
      imageHeight: (body['imageHeight'] as num?)?.toInt() ?? 0,
      imageBounds: _parseBounds(body['bounds']) ?? bounds,
    );
  }

  /// One date's class shares out of the `classBreakdown` object.
  ///
  /// Absent for a cached sub-area (the shares cannot be recounted from a
  /// cropped raster) and for a server predating the field, so an empty list is
  /// a normal outcome, not an error — the map still draws, without percentages.
  static List<LandCoverClass> _parseClasses(dynamic breakdown, String key) {
    if (breakdown is! Map) return const [];
    final rows = breakdown[key];
    if (rows is! List) return const [];
    return rows
        .whereType<Map<String, dynamic>>()
        .map(LandCoverClass.fromJson)
        .toList();
  }

  /// Decodes an optional base64 raster, tolerating a malformed one rather than
  /// failing a whole analysis over display-only imagery.
  static Uint8List? _decodePng(dynamic value) {
    if (value is! String || value.isEmpty) return null;
    try {
      return base64Decode(value);
    } catch (_) {
      return null;
    }
  }

  static AreaBounds? _parseBounds(dynamic value) {
    if (value is! Map) return null;
    final north = (value['north'] as num?)?.toDouble();
    final south = (value['south'] as num?)?.toDouble();
    final east = (value['east'] as num?)?.toDouble();
    final west = (value['west'] as num?)?.toDouble();
    if (north == null || south == null || east == null || west == null) {
      return null;
    }
    return AreaBounds(north: north, south: south, east: east, west: west);
  }

  void dispose() => _client.close();
}

/// Cells along the shorter side of the sample's synthetic pixel grid.
const int _sampleGridCells = 200;

/// Builds a synthetic, clearly-labelled result so the visualization can be seen
/// without a running backend or Sentinel Hub credentials.
///
/// Grows patches of "deforestation" (mask 1) and "water loss" (mask 2) inside
/// [bounds]. Points sit on a fixed grid and fill their patch solid, because
/// that is what a real run returns — one record per changed pixel of a raster,
/// not a cloud of free-floating coordinates. Painted as a mask they then read
/// the way the backend's own output does, ragged edge and all, rather than
/// dissolving into speckle.
///
/// This is **not** real satellite analysis — the UI marks any result with
/// `isSample == true` as sample data.
AnalysisResult sampleAnalysis({
  required AreaBounds bounds,
  required int oldYear,
  required int oldMonth,
  required int newYear,
  required int newMonth,
  int seed = 42,
}) {
  final rnd = math.Random(seed);
  final latSpan = (bounds.north - bounds.south).abs();
  final lngSpan = (bounds.east - bounds.west).abs();
  final latStep = latSpan / _sampleGridCells;
  final lngStep = lngSpan / _sampleGridCells;

  final changes = <ChangePoint>[];
  final taken = <int>{};

  /// Fills one patch centred on a grid cell.
  ///
  /// The radius is modulated per direction, so the outline comes out spiky and
  /// irregular the way a classifier's does — never a disc or a box.
  void addPatch({required int mask, required double radius}) {
    final centreRow = (0.2 + 0.6 * rnd.nextDouble()) * _sampleGridCells;
    final centreCol = (0.2 + 0.6 * rnd.nextDouble()) * _sampleGridCells;
    final wobble =
        List<double>.generate(24, (_) => 0.5 + rnd.nextDouble() * 0.8);
    final reach = (radius * 1.3).ceil();

    for (var dRow = -reach; dRow <= reach; dRow++) {
      for (var dCol = -reach; dCol <= reach; dCol++) {
        final distance = math.sqrt(dRow * dRow + dCol * dCol);
        final angle = math.atan2(dRow.toDouble(), dCol.toDouble());
        final sector =
            ((angle + math.pi) / (2 * math.pi) * wobble.length).floor();
        if (distance > radius * wobble[sector % wobble.length]) continue;

        final row = (centreRow + dRow).round();
        final col = (centreCol + dCol).round();
        if (row < 0 || row > _sampleGridCells) continue;
        if (col < 0 || col > _sampleGridCells) continue;
        // One record per cell: a pixel cannot change twice.
        if (!taken.add(row * (_sampleGridCells + 1) + col)) continue;

        changes.add(ChangePoint(
          location: LatLng(
            bounds.south + row * latStep,
            bounds.west + col * lngStep,
          ),
          mask: mask,
        ));
      }
    }
  }

  for (var i = 0; i < 3; i++) {
    addPatch(mask: 1, radius: 9 + rnd.nextDouble() * 11);
  }
  addPatch(mask: 2, radius: 7 + rnd.nextDouble() * 6);

  final deforestation = changes.where((c) => c.isDeforestation).length;
  final waterLoss = changes.where((c) => c.isWaterLoss).length;

  return AnalysisResult(
    message: 'Sample result — illustrative data, not a real satellite analysis.',
    changes: changes,
    isSample: true,
    imageBounds: bounds,
    stats: AnalysisStats(
      totalPixels: (_sampleGridCells + 1) * (_sampleGridCells + 1),
      deforestation: deforestation,
      waterLoss: waterLoss,
      oldDate: '$oldYear-${oldMonth.toString().padLeft(2, '0')}',
      newDate: '$newYear-${newMonth.toString().padLeft(2, '0')}',
    ),
  );
}
