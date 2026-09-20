import 'package:flutter_test/flutter_test.dart';
import 'package:ubiquitous_eye/models/analytics_service.dart';
import 'package:ubiquitous_eye/widgets/before_after_compare.dart';

void main() {
  group('service catalog', () {
    test('offers exactly the six services, in order', () {
      expect(
        kAnalyticsServices.map((s) => s.id).toList(),
        const [
          'deforestation',
          'urbanization',
          'surface_water_loss',
          'land_use_classification',
          'ndvi',
          'ndwi',
        ],
      );
    });

    test('the undirected services are gone', () {
      final ids = kAnalyticsServices.map((s) => s.id).toSet();
      // These asked "what differs" rather than "what was lost or gained", which
      // is not a question any of the detections can answer.
      expect(ids, isNot(contains('land_encroachment')));
      expect(ids, isNot(contains('water_body_encroachment')));
      expect(ids, isNot(contains('vegetation')));
    });

    test('every service has a name and a description', () {
      for (final s in kAnalyticsServices) {
        expect(s.name, isNotEmpty, reason: s.id);
        expect(s.description, isNotEmpty, reason: s.id);
        expect(s.categories, isNotEmpty, reason: s.id);
      }
    });
  });

  group('ChangeKind.forServiceId', () {
    test('maps each change service to the one change it detects', () {
      expect(ChangeKind.forServiceId('deforestation'),
          ChangeKind.deforestation);
      expect(ChangeKind.forServiceId('urbanization'), ChangeKind.urbanization);
      expect(ChangeKind.forServiceId('surface_water_loss'),
          ChangeKind.waterLoss);
    });

    test('single-date services and the generic flow pin nothing', () {
      // Null means "no single question asked", so every layer stays available.
      expect(ChangeKind.forServiceId('land_use_classification'), isNull);
      expect(ChangeKind.forServiceId('ndvi'), isNull);
      expect(ChangeKind.forServiceId('ndwi'), isNull);
      expect(ChangeKind.forServiceId(null), isNull);
      expect(ChangeKind.forServiceId('something-else'), isNull);
    });

    test('every change service in the catalog maps to a kind', () {
      const changeServices = {
        'deforestation',
        'urbanization',
        'surface_water_loss',
      };
      for (final id in changeServices) {
        expect(kAnalyticsServices.any((s) => s.id == id), isTrue,
            reason: '$id must exist in the catalog');
        expect(ChangeKind.forServiceId(id), isNotNull, reason: id);
      }
    });
  });
}
