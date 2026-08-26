import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:latlong2/latlong.dart';

/// A single search match — a named place at a location.
class PlaceResult {
  final String name;
  final LatLng location;

  const PlaceResult({required this.name, required this.location});

  /// First, most specific component of the full display name.
  String get shortName => name.split(',').first.trim();
}

/// Resolves the search bar query into map locations.
///
/// Two modes:
///  * Raw coordinates ("23.78, 90.40") are parsed locally — no network.
///  * Anything else is geocoded through OpenStreetMap's free Nominatim API.
class GeocodingService {
  GeocodingService({http.Client? client}) : _client = client ?? http.Client();

  final http.Client _client;

  // Nominatim's usage policy requires an identifying User-Agent.
  static const Map<String, String> _headers = {
    'User-Agent': 'UbiquitousEye/1.0 (flutter app)',
  };

  Future<List<PlaceResult>> search(String query) async {
    final trimmed = query.trim();
    if (trimmed.isEmpty) return const [];

    final coord = _tryParseCoordinates(trimmed);
    if (coord != null) {
      return [
        PlaceResult(
          name: 'Coordinates: '
              '${coord.latitude.toStringAsFixed(5)}, '
              '${coord.longitude.toStringAsFixed(5)}',
          location: coord,
        ),
      ];
    }

    final uri = Uri.https('nominatim.openstreetmap.org', '/search', {
      'q': trimmed,
      'format': 'json',
      'limit': '6',
    });

    final response = await _client.get(uri, headers: _headers);
    if (response.statusCode != 200) {
      throw Exception('Search failed (HTTP ${response.statusCode})');
    }

    final data = jsonDecode(response.body) as List<dynamic>;
    return data
        .whereType<Map<String, dynamic>>()
        .map(
          (m) => PlaceResult(
            name: (m['display_name'] as String?) ?? 'Unknown place',
            location: LatLng(
              double.parse(m['lat'] as String),
              double.parse(m['lon'] as String),
            ),
          ),
        )
        .toList();
  }

  /// Parses "lat, lng" / "lat lng" into a [LatLng] within valid ranges.
  LatLng? _tryParseCoordinates(String input) {
    final match = RegExp(r'^\s*(-?\d+(?:\.\d+)?)\s*[,\s]\s*(-?\d+(?:\.\d+)?)\s*$')
        .firstMatch(input);
    if (match == null) return null;

    final lat = double.tryParse(match.group(1)!);
    final lng = double.tryParse(match.group(2)!);
    if (lat == null || lng == null) return null;
    if (lat < -90 || lat > 90 || lng < -180 || lng > 180) return null;

    return LatLng(lat, lng);
  }
}
