import 'package:flutter/material.dart';

import '../../models/analytics_service.dart';
import '../../state/favourites.dart';
import '../../util/responsive.dart';
import '../../widgets/service_thumbnail.dart';
import 'analytics_image_options_page.dart';

// Keeps the description at a comfortable reading measure on wide screens.
const double _kDetailMaxWidth = 720;

const Color _ink = Color(0xFF0E1116);
const Color _accent = Color(0xFFEF9A3D);

/// Full description of a selected service with an Order action.
class ServiceDetailPage extends StatelessWidget {
  final AnalyticsService service;

  const ServiceDetailPage({super.key, required this.service});

  void _order(BuildContext context) {
    Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => AnalyticsImageOptionsPage(service: service),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.white,
      body: SafeArea(
        bottom: false,
        child: ResponsiveCenter(
          maxWidth: _kDetailMaxWidth,
          child: CustomScrollView(
            slivers: [
            SliverToBoxAdapter(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(8, 6, 16, 0),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    IconButton(
                      icon: const Icon(Icons.arrow_back, color: _ink),
                      onPressed: () => Navigator.of(context).maybePop(),
                    ),
                    const SizedBox(width: 4),
                    Expanded(
                      child: Padding(
                        padding: const EdgeInsets.only(top: 10),
                        child: Text(
                          service.name,
                          // Service names are long single words
                          // ("Deforestation") that cannot wrap, so between the
                          // two icon buttons a 26 px title runs off a small
                          // phone. Step the size down on narrow screens and cap
                          // the line count rather than overflowing.
                          maxLines: 3,
                          overflow: TextOverflow.ellipsis,
                          style: TextStyle(
                            fontSize: context.responsive(phone: 22, tablet: 26),
                            fontWeight: FontWeight.w800,
                            height: 1.1,
                            color: _ink,
                          ),
                        ),
                      ),
                    ),
                    _FavouriteToggle(id: service.id),
                  ],
                ),
              ),
            ),
            SliverToBoxAdapter(
              child: Padding(
                padding: const EdgeInsets.only(top: 8),
                child: SizedBox(
                  height: 200,
                  width: double.infinity,
                  child: ServiceThumbnail(service: service, iconSize: 72),
                ),
              ),
            ),
            SliverToBoxAdapter(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(20, 18, 20, 0),
                child: Row(
                  children: [
                    Icon(Icons.schedule, size: 20, color: Colors.grey.shade600),
                    const SizedBox(width: 10),
                    // Wraps instead of running off the edge once the label or
                    // the user's text scale outgrows a narrow phone.
                    Flexible(
                      child: Text(
                        service.delivery,
                        style: TextStyle(fontSize: 16, color: Colors.grey.shade700),
                      ),
                    ),
                  ],
                ),
              ),
            ),
            const SliverToBoxAdapter(
              child: Padding(
                padding: EdgeInsets.fromLTRB(20, 18, 20, 0),
                child: Divider(height: 1),
              ),
            ),
            SliverToBoxAdapter(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(20, 18, 20, 0),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text(
                      'Description',
                      style: TextStyle(
                        fontSize: 20,
                        fontWeight: FontWeight.w800,
                        color: _ink,
                      ),
                    ),
                    const SizedBox(height: 14),
                    Text(
                      service.description,
                      style: TextStyle(
                        fontSize: 15.5,
                        height: 1.55,
                        color: Colors.grey.shade800,
                      ),
                    ),
                    const SizedBox(height: 24),
                  ],
                ),
              ),
            ),
            ],
          ),
        ),
      ),
      bottomNavigationBar: _OrderBar(onOrder: () => _order(context)),
    );
  }
}

class _OrderBar extends StatelessWidget {
  final VoidCallback onOrder;

  const _OrderBar({required this.onOrder});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.fromLTRB(20, 12, 20, 12),
      decoration: BoxDecoration(
        color: Colors.white,
        border: Border(top: BorderSide(color: Colors.grey.shade200)),
      ),
      child: ResponsiveCenter(
        maxWidth: _kDetailMaxWidth,
        alignment: Alignment.center,
        // The Scaffold hands its bottom bar loose constraints; without this the
        // bar would stretch to the full screen height and leave the body none.
        heightFactor: 1,
        child: SizedBox(
          height: 54,
          width: double.infinity,
          child: ElevatedButton(
            onPressed: onOrder,
            style: ElevatedButton.styleFrom(
              backgroundColor: _ink,
              foregroundColor: Colors.white,
              elevation: 0,
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(12),
              ),
            ),
            child: const Text(
              'ORDER',
              style: TextStyle(
                fontSize: 16,
                fontWeight: FontWeight.w700,
                letterSpacing: 0.5,
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _FavouriteToggle extends StatelessWidget {
  final String id;

  const _FavouriteToggle({required this.id});

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<Set<String>>(
      valueListenable: favouriteServiceIds,
      builder: (context, favs, _) {
        final fav = favs.contains(id);
        return IconButton(
          iconSize: 26,
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
