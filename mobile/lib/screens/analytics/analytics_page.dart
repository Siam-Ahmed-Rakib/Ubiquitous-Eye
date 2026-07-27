import 'package:flutter/material.dart';

import '../../models/analytics_service.dart';
import '../../state/favourites.dart';
import '../../util/responsive.dart';
import '../../widgets/category_filter_bar.dart';
import '../../widgets/service_card.dart';
import '../../widgets/ubiquitous_eyes_logo.dart';
import 'service_detail_page.dart';

const Color _pageBg = Color(0xFFF3F4F6);

/// The Analytics catalog: brand wordmark, topic filters, and a grid of the
/// services Ubiquitous Eyes provides. Root of the Analytics tab's navigator.
class AnalyticsPage extends StatefulWidget {
  const AnalyticsPage({super.key});

  @override
  State<AnalyticsPage> createState() => _AnalyticsPageState();
}

class _AnalyticsPageState extends State<AnalyticsPage> {
  AnalyticsFilter? _filter;

  List<AnalyticsService> _visible(Set<String> favourites) {
    if (_filter == null) return kAnalyticsServices;
    if (_filter == AnalyticsFilter.favourites) {
      return kAnalyticsServices.where((s) => favourites.contains(s.id)).toList();
    }
    final category = _filter!.category!;
    return kAnalyticsServices
        .where((s) => s.categories.contains(category))
        .toList();
  }

  void _openService(AnalyticsService service) {
    Navigator.of(context).push(
      MaterialPageRoute(builder: (_) => ServiceDetailPage(service: service)),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.white,
      body: SafeArea(
        bottom: false,
        child: Column(
          children: [
            const Padding(
              padding: EdgeInsets.only(top: 14, bottom: 8),
              child: UbiquitousEyesLogo(),
            ),
            CategoryFilterBar(
              selected: _filter,
              onSelected: (f) => setState(() => _filter = f),
            ),
            Expanded(
              child: ValueListenableBuilder<Set<String>>(
                valueListenable: favouriteServiceIds,
                builder: (context, favourites, _) {
                  final items = _visible(favourites);
                  return Container(
                    width: double.infinity,
                    decoration: const BoxDecoration(
                      color: _pageBg,
                      borderRadius:
                          BorderRadius.vertical(top: Radius.circular(18)),
                    ),
                    child: items.isEmpty ? _emptyState() : _grid(items),
                  );
                },
              ),
            ),
          ],
        ),
      ),
    );
  }

  /// The catalog grid.
  ///
  /// Tile *height* is computed rather than expressed as a fixed
  /// `childAspectRatio`: a card is a thumbnail of fixed aspect stacked on a text
  /// block whose height depends on the user's text scale. A constant ratio makes
  /// the card too short as soon as text is scaled up, which is what used to push
  /// the heart button past the bottom edge.
  Widget _grid(List<AnalyticsService> items) {
    const double gap = 14;
    const EdgeInsets pad = EdgeInsets.fromLTRB(16, 18, 16, 16);

    return Center(
      child: ConstrainedBox(
        // Cap the grid width so cards don't stretch on very wide desktop windows.
        constraints: const BoxConstraints(maxWidth: 1180),
        child: LayoutBuilder(
          builder: (context, constraints) {
            final columns = context.responsive(phone: 2, tablet: 3, desktop: 4);
            final cardWidth =
                (constraints.maxWidth - pad.horizontal - gap * (columns - 1)) /
                    columns;

            final scaler = MediaQuery.textScalerOf(context);
            // Mirrors ServiceCard's own layout, so the two stay in step.
            final tileHeight = cardWidth / kServiceCardThumbAspect +
                kServiceCardTextBlockHeight(scaler);

            return GridView.builder(
              padding: pad,
              gridDelegate: SliverGridDelegateWithFixedCrossAxisCount(
                crossAxisCount: columns,
                mainAxisSpacing: gap,
                crossAxisSpacing: gap,
                mainAxisExtent: tileHeight,
              ),
              itemCount: items.length,
              itemBuilder: (context, i) => ServiceCard(
                service: items[i],
                onTap: () => _openService(items[i]),
              ),
            );
          },
        ),
      ),
    );
  }

  Widget _emptyState() {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.favorite_border, size: 44, color: Colors.grey.shade400),
            const SizedBox(height: 14),
            Text(
              'No favourites yet',
              style: TextStyle(
                fontSize: 17,
                fontWeight: FontWeight.w700,
                color: Colors.grey.shade700,
              ),
            ),
            const SizedBox(height: 6),
            Text(
              'Tap the heart on a service to save it here.',
              textAlign: TextAlign.center,
              style: TextStyle(fontSize: 14, color: Colors.grey.shade500),
            ),
          ],
        ),
      ),
    );
  }
}
