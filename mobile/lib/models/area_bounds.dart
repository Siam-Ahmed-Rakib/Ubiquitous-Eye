import 'dart:math' as math;

import 'package:latlong2/latlong.dart';

/// An axis-aligned area of interest defined by its geographic edges.
///
/// Stored as four bounds (instead of two corners) so each handle drag can
/// move exactly the two edges it owns while keeping the box rectangular.
class AreaBounds {
  final double north;
  final double south;
  final double east;
  final double west;

  const AreaBounds({
    required this.north,
    required this.south,
    required this.east,
    required this.west,
  });

  /// Builds a roughly [sizeKm] × [sizeKm] square centred on [center].
  factory AreaBounds.square({required LatLng center, double sizeKm = 5}) {
    final halfLat = (sizeKm / 2) / 111.0;
    final cosLat = math.cos(center.latitude * math.pi / 180).abs();
    final halfLng = (sizeKm / 2) / (111.0 * (cosLat == 0 ? 1e-6 : cosLat));
    return AreaBounds(
      north: center.latitude + halfLat,
      south: center.latitude - halfLat,
      east: center.longitude + halfLng,
      west: center.longitude - halfLng,
    );
  }

  LatLng get nw => LatLng(north, west);
  LatLng get ne => LatLng(north, east);
  LatLng get sw => LatLng(south, west);
  LatLng get se => LatLng(south, east);
  LatLng get center => LatLng((north + south) / 2, (east + west) / 2);
  LatLng get topCenter => LatLng(north, (east + west) / 2);
  LatLng get bottomCenter => LatLng(south, (east + west) / 2);
  LatLng get leftCenter => LatLng((north + south) / 2, west);
  LatLng get rightCenter => LatLng((north + south) / 2, east);

  /// Area in km², from the Haversine width × height of the box.
  double get areaKm2 {
    const distance = Distance();
    final width =
        distance.as(LengthUnit.Kilometer, LatLng(north, west), LatLng(north, east));
    final height =
        distance.as(LengthUnit.Kilometer, LatLng(north, west), LatLng(south, west));
    return width * height;
  }

  AreaBounds translated(double dLat, double dLng) => AreaBounds(
        north: north + dLat,
        south: south + dLat,
        east: east + dLng,
        west: west + dLng,
      );
}
