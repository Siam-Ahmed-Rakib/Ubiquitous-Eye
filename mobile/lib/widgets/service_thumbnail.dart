import 'package:flutter/material.dart';

import '../models/analytics_service.dart';

/// A generated thumbnail for a service: the service's gradient with a faint
/// raster grid overlaid (evoking a classified satellite layer) and the glyph.
class ServiceThumbnail extends StatelessWidget {
  final AnalyticsService service;
  final double iconSize;

  const ServiceThumbnail({super.key, required this.service, this.iconSize = 40});

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: BoxDecoration(
        gradient: LinearGradient(
          colors: service.gradient,
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
        ),
      ),
      child: CustomPaint(
        painter: _RasterGridPainter(),
        child: Center(
          child: Icon(
            service.icon,
            size: iconSize,
            color: Colors.white.withValues(alpha: 0.92),
          ),
        ),
      ),
    );
  }
}

class _RasterGridPainter extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    final line = Paint()
      ..color = Colors.white.withValues(alpha: 0.10)
      ..strokeWidth = 1;
    const step = 18.0;
    for (double x = step; x < size.width; x += step) {
      canvas.drawLine(Offset(x, 0), Offset(x, size.height), line);
    }
    for (double y = step; y < size.height; y += step) {
      canvas.drawLine(Offset(0, y), Offset(size.width, y), line);
    }
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}
