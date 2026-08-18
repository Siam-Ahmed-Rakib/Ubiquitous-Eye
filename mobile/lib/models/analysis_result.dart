import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:latlong2/latlong.dart';

import 'area_bounds.dart';
import 'land_use_result.dart';

/// Colour per `mask` value, matching `CHANGE_COLORS` in `server/api_server.py`.
const Color kDeforestationColor = Color(0xFFE53935); // mask 1 — vegetation loss
const Color kWaterLossColor = Color(0xFFFB8C00); // mask 2 — surface-water loss

/// The three ground types the before/after class map paints, in the order a
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
};

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

  /// The days each composite actually covers (e.g. `1–15 Jan 2025`). Change
  /// detection reads only the first half of the picked month, so the scenes are
  /// a narrower window than the month label implies — worth saying out loud.
  /// Empty when talking to a backend that predates the field.
  final String oldWindow;
  final String newWindow;

  const AnalysisStats({
    required this.totalPixels,
    required this.deforestation,
    required this.waterLoss,
    required this.oldDate,
    required this.newDate,
    this.oldWindow = '',
    this.newWindow = '',
  });

  factory AnalysisStats.fromJson(Map<String, dynamic> json) => AnalysisStats(
        totalPixels: (json['totalPixels'] as num?)?.toInt() ?? 0,
        deforestation: (json['deforestation'] as num?)?.toInt() ?? 0,
        waterLoss: (json['waterLoss'] as num?)?.toInt() ?? 0,
        oldDate: json['oldDate']?.toString() ?? '',
        newDate: json['newDate']?.toString() ?? '',
        oldWindow: json['oldWindow']?.toString() ?? '',
        newWindow: json['newWindow']?.toString() ?? '',
      );
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

  /// One raster per change class, so each can be toggled on its own.
  final Uint8List? deforestationPng;
  final Uint8List? waterLossPng;

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
    this.imageWidth = 0,
    this.imageHeight = 0,
    this.imageBounds,
    this.isSample = false,
  });

  int get deforestationCount =>
      stats?.deforestation ??
      changes.where((c) => c.isDeforestation).length;

  int get waterLossCount =>
      stats?.waterLoss ?? changes.where((c) => c.isWaterLoss).length;

  bool get hasChanges => changes.isNotEmpty;

  /// Whether both dates' scenes came back, so a before/after view is possible.
  bool get hasComparisonImagery => oldImagePng != null && newImagePng != null;

  /// Whether both dates' class maps came back, so the land-cover toggle has
  /// something to switch to. Independent of [hasComparisonImagery]: either can
  /// arrive without the other.
  bool get hasClassMaps => oldClassPng != null && newClassPng != null;

  /// Whether a changed-pixel raster exists to draw the difference view from.
  bool get hasChangeRasters =>
      deforestationPng != null || waterLossPng != null;

  /// Shape of the rasters. Guarded so a malformed response can't divide by zero.
  double get aspectRatio =>
      (imageWidth <= 0 || imageHeight <= 0) ? 1 : imageWidth / imageHeight;
}
