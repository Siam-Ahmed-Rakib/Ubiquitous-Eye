import 'package:flutter/foundation.dart';

/// In-memory set of favourited service ids.
///
/// Shared so the catalog grid, the favourites filter, and the detail screen
/// all stay in sync as the user taps hearts. Replaced with a new set on each
/// change so [ValueListenableBuilder] rebuilds.
final ValueNotifier<Set<String>> favouriteServiceIds =
    ValueNotifier<Set<String>>(<String>{});

void toggleFavourite(String id) {
  final next = Set<String>.from(favouriteServiceIds.value);
  if (!next.remove(id)) next.add(id);
  favouriteServiceIds.value = next;
}
