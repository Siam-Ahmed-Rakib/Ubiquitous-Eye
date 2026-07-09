import 'dart:convert';
import 'dart:math' as math;

import 'package:http/http.dart' as http;
import 'package:latlong2/latlong.dart';

import '../config.dart';
import '../models/analysis_result.dart';
import '../models/area_bounds.dart';

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

    return AnalysisResult(
      message: body['message']?.toString() ?? 'Analysis complete',
      changes: changes,
      stats: stats,
    );
  }

  void dispose() => _client.close();
}

/// Builds a synthetic, clearly-labelled result so the visualization can be seen
/// without a running backend or Sentinel Hub credentials.
///
/// Scatters clustered "deforestation" (mask 1) and "water loss" (mask 2) points
/// inside [bounds]. This is **not** real satellite analysis — the UI marks any
/// result with `isSample == true` as sample data.
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

  LatLng jitterAround(double cLat, double cLng, double spread) {
    return LatLng(
      cLat + (rnd.nextDouble() - 0.5) * latSpan * spread,
      cLng + (rnd.nextDouble() - 0.5) * lngSpan * spread,
    );
  }

  final changes = <ChangePoint>[];

  // A few deforestation clusters.
  const deforestClusters = 3;
  for (var c = 0; c < deforestClusters; c++) {
    final cLat = bounds.south + latSpan * (0.2 + 0.6 * rnd.nextDouble());
    final cLng = bounds.west + lngSpan * (0.2 + 0.6 * rnd.nextDouble());
    final count = 60 + rnd.nextInt(80);
    for (var i = 0; i < count; i++) {
      changes.add(ChangePoint(location: jitterAround(cLat, cLng, 0.18), mask: 1));
    }
  }

  // One smaller water-loss cluster.
  final wLat = bounds.south + latSpan * (0.3 + 0.4 * rnd.nextDouble());
  final wLng = bounds.west + lngSpan * (0.3 + 0.4 * rnd.nextDouble());
  final waterCount = 30 + rnd.nextInt(40);
  for (var i = 0; i < waterCount; i++) {
    changes.add(ChangePoint(location: jitterAround(wLat, wLng, 0.12), mask: 2));
  }

  final deforestation = changes.where((c) => c.isDeforestation).length;
  final waterLoss = changes.where((c) => c.isWaterLoss).length;

  return AnalysisResult(
    message: 'Sample result — illustrative data, not a real satellite analysis.',
    changes: changes,
    isSample: true,
    stats: AnalysisStats(
      totalPixels: changes.length + 900 + rnd.nextInt(400),
      deforestation: deforestation,
      waterLoss: waterLoss,
      oldDate: '$oldYear-${oldMonth.toString().padLeft(2, '0')}',
      newDate: '$newYear-${newMonth.toString().padLeft(2, '0')}',
    ),
  );
}
