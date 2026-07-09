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

/// The seven services Ubiquitous Eyes provides. Names chosen for clarity; the
/// detection intent the user described is captured in each description.
const List<AnalyticsService> kAnalyticsServices = [
  AnalyticsService(
    id: 'deforestation',
    name: 'Deforestation',
    categories: [ServiceCategory.environmental, ServiceCategory.agriculture],
    icon: Icons.forest_outlined,
    gradient: [Color(0xFF1B5E20), Color(0xFF66BB6A)],
    description:
        'Detects and measures forest loss over time by comparing multi-date '
        'satellite imagery. Newly cleared patches are highlighted, the rate of '
        'canopy loss is tracked, and likely illegal logging or land clearing is '
        'flagged inside your selected area. Built for conservation monitoring, '
        'carbon reporting, and enforcement.',
  ),
  AnalyticsService(
    id: 'land_encroachment',
    name: 'Land Encroachment',
    categories: [ServiceCategory.urbanPlanning, ServiceCategory.realEstate],
    icon: Icons.maps_home_work_outlined,
    gradient: [Color(0xFF37474F), Color(0xFF90A4AE)],
    description:
        'Identifies new construction on land that was previously empty or '
        'restricted. By comparing imagery across dates, the service flags '
        'buildings, structures, and paved surfaces that have appeared on vacant '
        'plots — supporting zoning enforcement, detection of unauthorised '
        'development, and land-rights monitoring.',
  ),
  AnalyticsService(
    id: 'water_body_encroachment',
    name: 'Water Body Encroachment',
    categories: [ServiceCategory.environmental, ServiceCategory.urbanPlanning],
    icon: Icons.water_outlined,
    gradient: [Color(0xFF01579B), Color(0xFF4FC3F7)],
    description:
        'Detects the filling, reclamation, or shrinking of rivers, canals, '
        'lakes, and wetlands. The service compares water extent across time to '
        'reveal where a water body has been filled in or built over, helping '
        'authorities protect floodplains, drainage, and natural water flow.',
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
        'Classifies every pixel of a scene into land-cover types — built-up, '
        'vegetation, water, bare soil, roads, and more. Delivered as a labelled '
        'raster, it gives a clear, up-to-date map of how land is used across '
        'your area of interest, ready for planning and change analysis.',
  ),
  AnalyticsService(
    id: 'vegetation',
    name: 'Vegetation',
    categories: [ServiceCategory.agriculture, ServiceCategory.environmental],
    icon: Icons.grass_outlined,
    gradient: [Color(0xFF2E7D32), Color(0xFF9CCC65)],
    description:
        'Maps the presence, density, and spread of vegetation across your area '
        'of interest. Green cover is separated from built and bare surfaces to '
        'give a clear picture of tree canopy, croplands, parks, and natural '
        'growth.',
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
