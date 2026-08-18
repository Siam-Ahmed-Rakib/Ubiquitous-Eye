import 'package:flutter/widgets.dart';
import 'package:flutter_map/flutter_map.dart';

/// One class raster and how strongly to paint it. Later entries draw on top.
class ChangeMaskImage {
  final ImageProvider image;
  final double opacity;

  const ChangeMaskImage({required this.image, this.opacity = 1});
}

/// Stretches change-mask rasters across [bounds] as a map layer.
///
/// The rasters span the whole analysed box and are transparent where nothing
/// changed, so a run paints its result over the entire area at once. That is
/// the point of drawing it this way: a cleared patch arrives as a solid shape
/// with the ragged outline the classifier gave it, rather than as one marker
/// per pixel — which, at any real change count, reads as a scatter of dots and
/// hides the shape of what was lost.
///
/// flutter_map's own [OverlayImage] would place the raster just as well, but it
/// exposes no [FilterQuality], so the default bilinear sampling feathers every
/// class boundary into a soft ramp as you zoom in. A mask cell is a
/// measurement, not a photograph — [FilterQuality.none] keeps its edges exactly
/// where they were measured.
class ChangeMaskLayer extends StatelessWidget {
  final LatLngBounds bounds;
  final List<ChangeMaskImage> masks;

  const ChangeMaskLayer({
    super.key,
    required this.bounds,
    required this.masks,
  });

  @override
  Widget build(BuildContext context) {
    if (masks.isEmpty) return const SizedBox.shrink();

    final camera = MapCamera.of(context);
    final topLeft = camera.getOffsetFromOrigin(bounds.northWest);
    final bottomRight = camera.getOffsetFromOrigin(bounds.southEast);

    return MobileLayerTransformer(
      child: ClipRect(
        child: Stack(
          children: [
            Positioned(
              left: topLeft.dx,
              top: topLeft.dy,
              width: bottomRight.dx - topLeft.dx,
              height: bottomRight.dy - topLeft.dy,
              child: Stack(
                fit: StackFit.expand,
                children: [
                  for (final mask in masks)
                    Opacity(
                      opacity: mask.opacity,
                      child: Image(
                        image: mask.image,
                        fit: BoxFit.fill,
                        filterQuality: FilterQuality.none,
                        gaplessPlayback: true,
                      ),
                    ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
