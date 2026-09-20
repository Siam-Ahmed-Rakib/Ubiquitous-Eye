import 'dart:typed_data';

import 'package:flutter/material.dart';

import 'area_bounds.dart';

/// The land-cover labels the backend classifier reports, and the colour the
/// overlay paints each one. Mirrors `LAND_COVER_COLORS` in `server/api_server.py`.
///
/// `Building` is split out of `Soil` by a trained sub-classifier — Sentinel-2's
/// "Bare Soil" scene class covers bare ground and built-up surfaces alike. It
/// takes the conventional cartographic red for built surfaces — a very dark
/// one. A mid red disappears into the imagery over a city, where rooftops,
/// brick kilns and dry ground are all reddish-brown at 10 m; nothing natural in
/// these scenes is this dark and this saturated, so it reads as an annotation
/// rather than as terrain.
///
/// The map draws the raster at partial opacity, so these stay fully saturated.
const Map<String, Color> kLandCoverPalette = {
  'Tree': Color(0xFF2E7D32),
  'Crop': Color(0xFF9CCC65),
  'Water': Color(0xFF1565C0),
  'Soil': Color(0xFFA1887F),
  'Building': Color(0xFF6D000A),
};

/// One land-cover type's share of a classified area.
class LandCoverClass {
  final String name;
  final Color color;
  final int pixels;
  final double percent;
  final double areaKm2;

  const LandCoverClass({
    required this.name,
    required this.color,
    required this.pixels,
    required this.percent,
    required this.areaKm2,
  });

  factory LandCoverClass.fromJson(Map<String, dynamic> json) {
    final name = json['name']?.toString() ?? '';
    return LandCoverClass(
      name: name,
      color: _parseHexColor(json['color']?.toString()) ??
          kLandCoverPalette[name] ??
          Colors.grey,
      pixels: (json['pixels'] as num?)?.toInt() ?? 0,
      percent: (json['percent'] as num?)?.toDouble() ?? 0,
      areaKm2: (json['areaKm2'] as num?)?.toDouble() ?? 0,
    );
  }
}

/// Parses `#RRGGBB`, returning null if the backend sent something unexpected.
Color? _parseHexColor(String? hex) {
  if (hex == null) return null;
  final cleaned = hex.replaceFirst('#', '');
  if (cleaned.length != 6) return null;
  final value = int.tryParse(cleaned, radix: 16);
  return value == null ? null : Color(0xFF000000 | value);
}

/// A parsed response from `POST /api/sentinel/classify`.
///
/// [imagePng] is an RGBA PNG spanning exactly [bounds]: one pixel block per
/// classified cell, transparent wherever the composite had no cloud-free data.
///
/// [baseImagePng] is a true-colour render of the very Sentinel-2 composite the
/// classifier consumed, on the same grid and at the same size, so the two stack
/// without resampling. The UI draws the mask over it rather than over a basemap.
class LandUseResult {
  final Uint8List imagePng;
  final Uint8List? baseImagePng;
  final int imageWidth;
  final int imageHeight;
  final AreaBounds bounds;
  final List<LandCoverClass> classes;

  /// Composite date, as `YYYY-MM`.
  final String date;

  /// Ground resolution of one classified cell, in metres. Large areas are
  /// strided down, so this can exceed the native 30 m.
  final int resolutionMeters;

  final int totalCells;
  final int classifiedCells;
  final double areaKm2;

  /// True when generated locally for demonstration — no satellite data was
  /// involved. Surfaced prominently in the UI.
  final bool isSample;

  const LandUseResult({
    required this.imagePng,
    required this.baseImagePng,
    required this.imageWidth,
    required this.imageHeight,
    required this.bounds,
    required this.classes,
    required this.date,
    required this.resolutionMeters,
    required this.totalCells,
    required this.classifiedCells,
    required this.areaKm2,
    this.isSample = false,
  });

  /// Classes that actually occur, largest first.
  List<LandCoverClass> get presentClasses =>
      classes.where((c) => c.pixels > 0).toList();

  bool get hasCoverage => classifiedCells > 0;

  /// Shape of both rasters. Guarded so a malformed response can't divide by zero.
  double get aspectRatio =>
      (imageWidth <= 0 || imageHeight <= 0) ? 1 : imageWidth / imageHeight;

  /// Share of the area the classifier could label; the rest was cloud or gap.
  double get coveragePercent =>
      totalCells == 0 ? 0 : 100.0 * classifiedCells / totalCells;
}
