import 'package:flutter/material.dart';

/// Topic areas a service can belong to. Used by the catalog filter strip.
enum ServiceCategory { agriculture, realEstate, urbanPlanning, environmental }

/// The filter chips shown above the catalog. `favourites` is a saved-items
/// view rather than a real category, so it maps to no [ServiceCategory].
enum AnalyticsFilter { favourites, agriculture, realEstate, urbanPlanning, environmental }

extension AnalyticsFilterX on AnalyticsFilter {
  String get label => switch (this) {
        AnalyticsFilter.favourites => 'Favourites',
        AnalyticsFilter.agriculture => 'Agriculture',
        AnalyticsFilter.realEstate => 'Real Estate',
        AnalyticsFilter.urbanPlanning => 'Urban Planning',
        AnalyticsFilter.environmental => 'Environmental',
      };

  IconData get icon => switch (this) {
        AnalyticsFilter.favourites => Icons.favorite_border,
        AnalyticsFilter.agriculture => Icons.agriculture_outlined,
        AnalyticsFilter.realEstate => Icons.home_outlined,
        AnalyticsFilter.urbanPlanning => Icons.location_city_outlined,
        AnalyticsFilter.environmental => Icons.eco_outlined,
      };

  /// The topic this filter maps to, or null for the favourites view.
  ServiceCategory? get category => switch (this) {
        AnalyticsFilter.favourites => null,
        AnalyticsFilter.agriculture => ServiceCategory.agriculture,
        AnalyticsFilter.realEstate => ServiceCategory.realEstate,
        AnalyticsFilter.urbanPlanning => ServiceCategory.urbanPlanning,
        AnalyticsFilter.environmental => ServiceCategory.environmental,
      };
}

/// One analytics product offered in the catalog.
class AnalyticsService {
  final String id;
  final String name;
  final String delivery;
  final String description;
  final List<ServiceCategory> categories;
  final IconData icon;
  final List<Color> gradient;

  const AnalyticsService({
    required this.id,
    required this.name,
    required this.description,
    required this.categories,
    required this.icon,
    required this.gradient,
    this.delivery = '3 day delivery',
  });
}

/// The six services Ubiquitous Eyes provides.
///
/// Three are change detections over a pair of dates, and each is *directional* —
/// it asks what was lost or gained, not merely what differs. Three are
/// single-date products. Nothing here is a generic "difference" service: an
/// undirected comparison cannot answer any of the questions these are for.
const List<AnalyticsService> kAnalyticsServices = [
  AnalyticsService(
    id: 'deforestation',
    name: 'Deforestation',
    categories: [ServiceCategory.environmental, ServiceCategory.agriculture],
    icon: Icons.forest_outlined,
    gradient: [Color(0xFF1B5E20), Color(0xFF66BB6A)],
    description:
        'Finds forest that has gone. A pixel is reported when it was tree cover '
        'at the earlier date and is not tree cover now, whatever replaced it — '
        'newly grown canopy is deliberately not counted. Built for conservation '
        'monitoring, carbon reporting, and enforcement against illegal logging '
        'or land clearing.',
  ),
  AnalyticsService(
    id: 'urbanization',
    name: 'Urbanization',
    categories: [ServiceCategory.urbanPlanning, ServiceCategory.realEstate],
    icon: Icons.location_city_outlined,
    gradient: [Color(0xFF4A148C), Color(0xFFBA68C8)],
    description:
        'Finds ground that has been built on since the earlier date. Only new '
        'construction counts: a pixel is reported when there was no building '
        'before and there is one now, so long-standing built-up areas stay '
        'quiet and the map shows growth rather than extent. Built for zoning '
        'enforcement, unauthorised-development detection, and tracking how a '
        'city spreads.',
  ),
  AnalyticsService(
    id: 'surface_water_loss',
    name: 'Surface Water Loss Detection',
    categories: [ServiceCategory.environmental, ServiceCategory.urbanPlanning],
    icon: Icons.water_outlined,
    gradient: [Color(0xFF01579B), Color(0xFF4FC3F7)],
    description:
        'Finds water that has disappeared. A pixel is reported when it was '
        'water at the earlier date and is not water now — whether it was '
        'filled, drained, or built over — while newly flooded ground is left '
        'out. Reveals shrinking rivers, canals, lakes, and wetlands for '
        'floodplain and drainage protection.',
  ),
  AnalyticsService(
    id: 'land_use_classification',
    name: 'Land Use Classification',
    categories: [
      ServiceCategory.urbanPlanning,
      ServiceCategory.realEstate,
      ServiceCategory.environmental,
    ],
    icon: Icons.layers_outlined,
    gradient: [Color(0xFFEF6C00), Color(0xFF26A69A)],
    description:
        'Classifies every pixel of a scene into land-cover types — tree cover, '
        'crops, water bodies, and bare soil. Delivered as a labelled raster '
        'drawn over the map, it gives a clear, up-to-date picture of how land '
        'is used across your area of interest, ready for planning and change '
        'analysis.',
  ),
  AnalyticsService(
    id: 'ndvi',
    name: 'NDVI — Vegetation Health',
    categories: [ServiceCategory.agriculture, ServiceCategory.environmental],
    icon: Icons.spa_outlined,
    gradient: [Color(0xFF558B2F), Color(0xFFCDDC39)],
    description:
        'Computes the Normalised Difference Vegetation Index to measure plant '
        'vigour. High values mark dense, healthy vegetation while low values '
        'reveal stress, sparse growth, or bare ground — ideal for agriculture, '
        'drought watch, and crop management.',
  ),
  AnalyticsService(
    id: 'ndwi',
    name: 'NDWI — Standing Water',
    categories: [ServiceCategory.environmental, ServiceCategory.agriculture],
    icon: Icons.water_drop_outlined,
    gradient: [Color(0xFF006064), Color(0xFF26C6DA)],
    description:
        'Computes the Normalised Difference Water Index to detect surface water '
        'and moisture. Standing water, waterlogging, and saturated ground are '
        'clearly delineated — useful for flood mapping, irrigation planning, '
        'and water-body monitoring.',
  ),
];
