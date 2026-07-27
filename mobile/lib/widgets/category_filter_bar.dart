import 'package:flutter/material.dart';

import '../models/analytics_service.dart';

const Color _ink = Color(0xFF0E1116);
const Color _accent = Color(0xFFEF9A3D);

/// Horizontal strip of topic filters above the catalog. Tapping the active
/// chip again clears the filter (shows all services).
class CategoryFilterBar extends StatelessWidget {
  final AnalyticsFilter? selected;
  final ValueChanged<AnalyticsFilter?> onSelected;

  const CategoryFilterBar({
    super.key,
    required this.selected,
    required this.onSelected,
  });

  @override
  Widget build(BuildContext context) {
    const filters = AnalyticsFilter.values;
    // The chip has to fit the longest label ("Environmental") on one line at the
    // user's text scale; at a fixed width it broke mid-word into "Environmenta"
    // + "l". Height follows for the same reason.
    final scaler = MediaQuery.textScalerOf(context);
    final chipWidth = scaler.scale(12) * 8.0;
    final barHeight = 30 + 7 + scaler.scale(12) * 1.1 * 2 + 16;

    return SizedBox(
      height: barHeight,
      child: ListView.separated(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: 20),
        itemCount: filters.length,
        separatorBuilder: (_, __) => const SizedBox(width: 26),
        itemBuilder: (context, i) {
          final filter = filters[i];
          final active = filter == selected;
          final icon = (active && filter == AnalyticsFilter.favourites)
              ? Icons.favorite
              : filter.icon;
          return InkWell(
            onTap: () => onSelected(active ? null : filter),
            borderRadius: BorderRadius.circular(12),
            child: SizedBox(
              width: chipWidth,
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Icon(icon, size: 30, color: active ? _accent : Colors.grey.shade500),
                  const SizedBox(height: 7),
                  Text(
                    filter.label,
                    textAlign: TextAlign.center,
                    maxLines: 2,
                    style: TextStyle(
                      fontSize: 12,
                      height: 1.1,
                      fontWeight: active ? FontWeight.w700 : FontWeight.w400,
                      color: active ? _ink : Colors.grey.shade600,
                    ),
                  ),
                ],
              ),
            ),
          );
        },
      ),
    );
  }
}
