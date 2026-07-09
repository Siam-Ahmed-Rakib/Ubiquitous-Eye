import 'package:latlong2/latlong.dart';

/// A single changed pixel from the backend change-detection pipeline.
///
/// The backend encodes the kind of change in `mask`:
///  * `1` → forest / vegetation loss (deforestation)
///  * `2` → surface-water loss
class ChangePoint {
  final LatLng location;
  final int mask;

  const ChangePoint({required this.location, required this.mask});

  bool get isDeforestation => mask == 1;
  bool get isWaterLoss => mask == 2;
}

/// Aggregate counts for one analysis run (mirrors the backend `stats` object).
class AnalysisStats {
  final int totalPixels;
  final int deforestation;
  final int waterLoss;
  final String oldDate;
  final String newDate;

  const AnalysisStats({
    required this.totalPixels,
    required this.deforestation,
    required this.waterLoss,
    required this.oldDate,
    required this.newDate,
  });

  factory AnalysisStats.fromJson(Map<String, dynamic> json) => AnalysisStats(
        totalPixels: (json['totalPixels'] as num?)?.toInt() ?? 0,
        deforestation: (json['deforestation'] as num?)?.toInt() ?? 0,
        waterLoss: (json['waterLoss'] as num?)?.toInt() ?? 0,
        oldDate: json['oldDate']?.toString() ?? '',
        newDate: json['newDate']?.toString() ?? '',
      );
}

/// Parsed response from `POST /api/sentinel/analyze`.
class AnalysisResult {
  final String message;
  final List<ChangePoint> changes;
  final AnalysisStats? stats;

  /// True when this result was generated locally for demonstration (no real
  /// satellite data / backend involved). Surfaced prominently in the UI.
  final bool isSample;

  const AnalysisResult({
    required this.message,
    required this.changes,
    required this.stats,
    this.isSample = false,
  });

  int get deforestationCount =>
      stats?.deforestation ??
      changes.where((c) => c.isDeforestation).length;

  int get waterLossCount =>
      stats?.waterLoss ?? changes.where((c) => c.isWaterLoss).length;

  bool get hasChanges => changes.isNotEmpty;
}
