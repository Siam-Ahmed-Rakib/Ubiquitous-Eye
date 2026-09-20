import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:latlong2/latlong.dart';

import 'area_bounds.dart';
import 'land_use_result.dart';

/// Colour per `mask` value, matching `CHANGE_COLORS` in `server/api_server.py`.
const Color kDeforestationColor = Color(0xFFE53935); // mask 1 — vegetation loss
const Color kWaterLossColor = Color(0xFFFB8C00); // mask 2 — surface-water loss
const Color kUrbanizationColor = Color(0xFF8E24AA); // mask 3 — new built-up

/// The four ground types the before/after class map paints, in the order a
/// legend should list them.
///
/// Each colour is the *mid* stop of that class's ramp in `CLASS_MAP_RAMPS`
/// (`server/api_server.py`) — the rendered map shades darker and lighter around
/// it with the scene's own brightness, so this is the colour that represents the
/// class rather than one the raster necessarily contains.
const Map<String, Color> kClassMapPalette = {
  'Tree': Color(0xFF2D7D32),
  'Water': Color(0xFF1565C0),
  'Soil': Color(0xFFB08968),
  'Building': Color(0xFF6D000A),
};

/// A single changed pixel from the backend change-detection pipeline.
///
/// The backend encodes the kind of change in `mask`:
///  * `1` → forest / vegetation loss (deforestation)
///  * `3` → new built-up surface (urbanisation)
///  * `2` → surface-water loss
class ChangePoint {
  final LatLng location;
  final int mask;

  const ChangePoint({required this.location, required this.mask});

  bool get isDeforestation => mask == 1;
  bool get isWaterLoss => mask == 2;
  bool get isUrbanization => mask == 3;
}

/// Aggregate counts for one analysis run (mirrors the backend `stats` object).
class AnalysisStats {
  final int totalPixels;

  /// Pixels meeting the clear-observation requirement on both dates. Null when
  /// talking to an older backend that did not report data quality separately.
  final int? eligiblePixels;
  final int uncertainPixels;
  final int minimumClearObservations;

  final int deforestation;
  final int waterLoss;
  final int urbanization;
  final String oldDate;
  final String newDate;

  /// The days each composite actually covers. Normally this is the full picked
  /// month; it can include adjacent dates when cloud forces adaptive expansion.
  /// Empty when talking to a backend that predates the field.
  final String oldWindow;
  final String newWindow;

  const AnalysisStats({
    required this.totalPixels,
    this.eligiblePixels,
    this.uncertainPixels = 0,
    this.minimumClearObservations = 0,
    required this.deforestation,
    required this.waterLoss,
    required this.urbanization,
    required this.oldDate,
    required this.newDate,
    this.oldWindow = '',
    this.newWindow = '',
  });

  factory AnalysisStats.fromJson(Map<String, dynamic> json) => AnalysisStats(
        totalPixels: (json['totalPixels'] as num?)?.toInt() ?? 0,
        eligiblePixels: (json['eligiblePixels'] as num?)?.toInt(),
        uncertainPixels: (json['uncertainPixels'] as num?)?.toInt() ?? 0,
        minimumClearObservations:
            (json['minimumClearObservations'] as num?)?.toInt() ?? 0,
        deforestation: (json['deforestation'] as num?)?.toInt() ?? 0,
        waterLoss: (json['waterLoss'] as num?)?.toInt() ?? 0,
        urbanization: (json['urbanization'] as num?)?.toInt() ?? 0,
        oldDate: json['oldDate']?.toString() ?? '',
        newDate: json['newDate']?.toString() ?? '',
        oldWindow: json['oldWindow']?.toString() ?? '',
        newWindow: json['newWindow']?.toString() ?? '',
      );
}

/// One region where a change was found, as the backend clustered it.
///
/// The changed cells are grouped and each group described by the ellipse that
/// fits it, so a result names places rather than listing thousands of pixels.
/// [centre] is what goes in a report; the ellipse is what gets drawn.
class ChangeRegion {
  final LatLng centre;
  final double semiMajorDeg;
  final double semiMinorDeg;
  final double angleDeg;

  /// How many changed cells this region covers.
  final int points;

  /// The centre in Google Maps order and formatting — `lat, lng`. Comes from
  /// the backend already formatted, so the lat/lng swap that drops a pin in the
  /// wrong hemisphere can only be got wrong in one place.
  final String coordinate;

  /// A maps link to the centre, ready to open or paste.
  final String googleMapsUrl;

  const ChangeRegion({
    required this.centre,
    required this.semiMajorDeg,
    required this.semiMinorDeg,
    required this.angleDeg,
    required this.points,
    required this.coordinate,
    required this.googleMapsUrl,
  });

  factory ChangeRegion.fromJson(Map<String, dynamic> json) => ChangeRegion(
        centre: LatLng(
          (json['centerLat'] as num?)?.toDouble() ?? 0,
          (json['centerLon'] as num?)?.toDouble() ?? 0,
        ),
        semiMajorDeg: (json['semiMajorDeg'] as num?)?.toDouble() ?? 0,
        semiMinorDeg: (json['semiMinorDeg'] as num?)?.toDouble() ?? 0,
        angleDeg: (json['angleDeg'] as num?)?.toDouble() ?? 0,
        points: (json['points'] as num?)?.toInt() ?? 0,
        coordinate: json['coordinate']?.toString() ??
            '${((json['centerLat'] as num?)?.toDouble() ?? 0).toStringAsFixed(6)}, '
                '${((json['centerLon'] as num?)?.toDouble() ?? 0).toStringAsFixed(6)}',
        googleMapsUrl: json['googleMapsUrl']?.toString() ?? '',
      );

  /// What to show: the Google Maps coordinate, which pastes straight into the
  /// search box. A decorated `23.8012° N` form reads nicely and cannot be
  /// pasted anywhere useful, which is the wrong trade for a field report.
  String get label => coordinate;

  static List<ChangeRegion> listFrom(dynamic raw) {
    if (raw is! List) return const [];
    return raw
        .whereType<Map>()
        .map((e) => ChangeRegion.fromJson(Map<String, dynamic>.from(e)))
        .toList();
  }
}

/// Parsed response from `POST /api/sentinel/analyze`.
///
/// Beyond the changed-pixel list, the backend may return three pixel-aligned
/// RGBA PNGs spanning [imageBounds]: a cloud-masked true-colour scene for each
/// date, and the change mask to draw over them. They register cell for cell, so
/// the UI can show before/after side by side with the mask on top. Imagery is
/// display-only and optional — when the fetch fails the analysis still returns
/// and the UI falls back to plotting points on the map.
class AnalysisResult {
  final String message;
  final List<ChangePoint> changes;
  final AnalysisStats? stats;

  final Uint8List? oldImagePng;
  final Uint8List? newImagePng;

  /// Each date's land cover painted as a picture: every cell coloured by what
  /// the classifier called it (green tree, blue water, tan soil), shaded by the
  /// scene's own brightness and outlined where two classes meet.
  ///
  /// The same grid and bounds as the scenes above, so a pane can swap between
  /// raw imagery and its class map without anything moving.
  final Uint8List? oldClassPng;
  final Uint8List? newClassPng;

  /// What each date is made of, largest share first. Empty when the backend
  /// could not report it — a cached sub-area, or an older server.
  final List<LandCoverClass> oldClasses;
  final List<LandCoverClass> newClasses;

  /// One raster per change class, so the screen can show exactly the one the
  /// chosen service is about.
  ///
  /// They are not mutually exclusive: ground that was cleared and then built on
  /// appears in both [deforestationPng] and [urbanizationPng], because it is
  /// genuinely both.
  final Uint8List? deforestationPng;
  final Uint8List? waterLossPng;
  final Uint8List? urbanizationPng;

  /// Where each change happened, largest region first. Empty when the change
  /// was too sparse to describe as regions, or when an older server answered.
  final List<ChangeRegion> deforestationRegions;
  final List<ChangeRegion> waterLossRegions;
  final List<ChangeRegion> urbanizationRegions;

  final int imageWidth;
  final int imageHeight;
  final AreaBounds? imageBounds;

  /// True when this result was generated locally for demonstration (no real
  /// satellite data / backend involved). Surfaced prominently in the UI.
  final bool isSample;

  const AnalysisResult({
    required this.message,
    required this.changes,
    required this.stats,
    this.oldImagePng,
    this.newImagePng,
    this.oldClassPng,
    this.newClassPng,
    this.oldClasses = const [],
    this.newClasses = const [],
    this.deforestationPng,
    this.waterLossPng,
    this.urbanizationPng,
    this.deforestationRegions = const [],
    this.waterLossRegions = const [],
    this.urbanizationRegions = const [],
    this.imageWidth = 0,
    this.imageHeight = 0,
    this.imageBounds,
    this.isSample = false,
  });

  int get deforestationCount =>
      stats?.deforestation ?? changes.where((c) => c.isDeforestation).length;

  int get waterLossCount =>
      stats?.waterLoss ?? changes.where((c) => c.isWaterLoss).length;

  int get urbanizationCount =>
      stats?.urbanization ?? changes.where((c) => c.isUrbanization).length;

  bool get hasChanges => changes.isNotEmpty;

  /// Whether both dates' scenes came back, so a before/after view is possible.
  bool get hasComparisonImagery => oldImagePng != null && newImagePng != null;

  /// Whether both dates' class maps came back, so the land-cover toggle has
  /// something to switch to. Independent of [hasComparisonImagery]: either can
  /// arrive without the other.
  bool get hasClassMaps => oldClassPng != null && newClassPng != null;

  /// Whether a changed-pixel raster exists to draw the difference view from.
  bool get hasChangeRasters =>
      deforestationPng != null ||
      waterLossPng != null ||
      urbanizationPng != null;

  /// Shape of the rasters. Guarded so a malformed response can't divide by zero.
  double get aspectRatio =>
      (imageWidth <= 0 || imageHeight <= 0) ? 1 : imageWidth / imageHeight;
}
