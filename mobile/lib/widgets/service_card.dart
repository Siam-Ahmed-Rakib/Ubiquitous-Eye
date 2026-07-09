import 'package:flutter/material.dart';

import '../models/analytics_service.dart';
import '../state/favourites.dart';
import 'service_thumbnail.dart';

const Color _ink = Color(0xFF0E1116);
const Color _accent = Color(0xFFEF9A3D);

/// A catalog grid tile for one analytics service.
class ServiceCard extends StatelessWidget {
  final AnalyticsService service;
  final VoidCallback onTap;

  const ServiceCard({super.key, required this.service, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.white,
      borderRadius: BorderRadius.circular(14),
      clipBehavior: Clip.antiAlias,
      elevation: 1.5,
      shadowColor: Colors.black26,
      child: InkWell(
        onTap: onTap,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            AspectRatio(
              aspectRatio: 1.55,
              child: ServiceThumbnail(service: service),
            ),
            Expanded(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(12, 10, 8, 6),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Icon(Icons.schedule, size: 14, color: Colors.grey.shade500),
                        const SizedBox(width: 5),
                        Text(
                          service.delivery,
                          style: TextStyle(fontSize: 12, color: Colors.grey.shade600),
                        ),
                      ],
                    ),
                    const SizedBox(height: 6),
                    Text(
                      service.name,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                        fontSize: 15,
                        fontWeight: FontWeight.w700,
                        height: 1.15,
                        color: _ink,
                      ),
                    ),
                    const Spacer(),
                    Align(
                      alignment: Alignment.centerRight,
                      child: _FavouriteButton(id: service.id),
                    ),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// Heart toggle bound to the shared favourites store.
class _FavouriteButton extends StatelessWidget {
  final String id;

  const _FavouriteButton({required this.id});

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<Set<String>>(
      valueListenable: favouriteServiceIds,
      builder: (context, favs, _) {
        final fav = favs.contains(id);
        return IconButton(
          visualDensity: VisualDensity.compact,
          padding: EdgeInsets.zero,
          constraints: const BoxConstraints(minWidth: 36, minHeight: 36),
          iconSize: 22,
          tooltip: fav ? 'Remove from favourites' : 'Add to favourites',
          icon: Icon(
            fav ? Icons.favorite : Icons.favorite_border,
            color: fav ? _accent : Colors.grey.shade400,
          ),
          onPressed: () => toggleFavourite(id),
        );
      },
    );
  }
}
