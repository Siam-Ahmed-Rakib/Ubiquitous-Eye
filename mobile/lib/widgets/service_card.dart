import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../models/analytics_service.dart';
import '../state/favourites.dart';
import 'service_thumbnail.dart';

const Color _ink = Color(0xFF0E1116);
const Color _accent = Color(0xFFEF9A3D);

/// Width : height of the thumbnail band at the top of a [ServiceCard].
const double kServiceCardThumbAspect = 1.55;

/// Height of everything below the thumbnail, at the given text scale.
///
/// The catalog grid needs this to size its tiles: a tile is the thumbnail (a
/// fixed aspect of the tile width) plus this block, and the block grows with the
/// user's font-size setting. Deliberately errs high — a few slack pixels are
/// invisible, an underestimate paints an overflow stripe.
double kServiceCardTextBlockHeight(TextScaler scaler) {
  const double verticalPadding = 10 + 6; // Padding.fromLTRB(12, 10, 8, 6)
  const double gapBelowDelivery = 6;
  const double favouriteButtonHeight = 36; // _FavouriteButton's min constraint
  // The delivery row is as tall as its icon or its (scaled) text, whichever
  // wins. 1.35 covers the tallest line height a fallback font is likely to use.
  final deliveryRow = math.max(14.0, scaler.scale(12) * 1.35);
  // The name is capped at two lines with an explicit height factor of 1.15.
  final nameBlock = scaler.scale(15) * 1.15 * 2;
  return verticalPadding +
      deliveryRow +
      gapBelowDelivery +
      nameBlock +
      favouriteButtonHeight;
}

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
              aspectRatio: kServiceCardThumbAspect,
              child: ServiceThumbnail(service: service),
            ),
            Expanded(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(12, 10, 8, 6),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  // Pushes the heart to the bottom without a Spacer, which
                  // would demand free space the tile may not have.
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Row(
                      children: [
                        Icon(Icons.schedule, size: 14, color: Colors.grey.shade500),
                        const SizedBox(width: 5),
                        // Flexible + ellipsis: the tile is only ~140 px wide on
                        // a small phone, so an unconstrained label here runs off
                        // the right edge as soon as the string or the user's
                        // text scale grows.
                        Flexible(
                          child: Text(
                            service.delivery,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: TextStyle(fontSize: 12, color: Colors.grey.shade600),
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 6),
                    Flexible(
                      child: Text(
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
                    ),
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
