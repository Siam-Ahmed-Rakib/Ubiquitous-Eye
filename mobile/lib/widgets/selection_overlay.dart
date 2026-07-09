import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
// latlong2 exports its own `Path` type; hide it so `Path` below means dart:ui.Path.
import 'package:latlong2/latlong.dart' hide Path;

import '../models/area_bounds.dart';

enum _Corner { nw, ne, sw, se }

const Color _accent = Color(0xFFEF9A3D);

/// A flutter_map child layer that draws the selection rectangle and its
/// interactive handles.
///
/// Because it reads [MapCamera.of] it rebuilds on every pan/zoom, so the box
/// stays anchored to the geography. Corner handles resize the box; the centre
/// handle moves it. Each handle reports drag start/end so the parent can
/// freeze map gestures while a handle is being dragged.
class AreaSelectionOverlay extends StatelessWidget {
  final AreaBounds bounds;
  final ValueChanged<AreaBounds> onChanged;
  final VoidCallback onDragStart;
  final VoidCallback onDragEnd;

  const AreaSelectionOverlay({
    super.key,
    required this.bounds,
    required this.onChanged,
    required this.onDragStart,
    required this.onDragEnd,
  });

  static const double _cornerSize = 24;
  static const double _moveSize = 42;

  // Minimum span between opposite edges (~90 m) so the box can't invert.
  static const double _minSep = 0.0008;

  @override
  Widget build(BuildContext context) {
    final camera = MapCamera.of(context);

    Offset toOffset(LatLng p) {
      final pt = camera.latLngToScreenPoint(p);
      return Offset(pt.x, pt.y);
    }

    final nw = toOffset(bounds.nw);
    final ne = toOffset(bounds.ne);
    final sw = toOffset(bounds.sw);
    final se = toOffset(bounds.se);
    final topCenter = toOffset(bounds.topCenter);
    final center = toOffset(bounds.center);

    return Stack(
      children: [
        // Fill + border, drawn in screen space.
        Positioned.fill(
          child: IgnorePointer(
            child: CustomPaint(
              painter: _RectPainter(nw: nw, ne: ne, se: se, sw: sw),
            ),
          ),
        ),

        // Area badge straddling the top edge.
        Positioned(
          left: topCenter.dx,
          top: topCenter.dy,
          child: FractionalTranslation(
            translation: const Offset(-0.5, -0.5),
            child: IgnorePointer(child: _AreaBadge(areaKm2: bounds.areaKm2)),
          ),
        ),

        // Move handle (centre) + four resize handles.
        _moveHandle(center, camera),
        _cornerHandle(_Corner.nw, nw, camera),
        _cornerHandle(_Corner.ne, ne, camera),
        _cornerHandle(_Corner.sw, sw, camera),
        _cornerHandle(_Corner.se, se, camera),
      ],
    );
  }

  Widget _cornerHandle(_Corner corner, Offset pos, MapCamera camera) {
    return Positioned(
      left: pos.dx - _cornerSize / 2,
      top: pos.dy - _cornerSize / 2,
      width: _cornerSize,
      height: _cornerSize,
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onPanStart: (_) => onDragStart(),
        onPanUpdate: (d) => _dragCorner(corner, d.delta, camera),
        onPanEnd: (_) => onDragEnd(),
        onPanCancel: onDragEnd,
        child: const _HandleDot(),
      ),
    );
  }

  Widget _moveHandle(Offset pos, MapCamera camera) {
    return Positioned(
      left: pos.dx - _moveSize / 2,
      top: pos.dy - _moveSize / 2,
      width: _moveSize,
      height: _moveSize,
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onPanStart: (_) => onDragStart(),
        onPanUpdate: (d) => _dragMove(d.delta, camera),
        onPanEnd: (_) => onDragEnd(),
        onPanCancel: onDragEnd,
        child: const _MoveDot(),
      ),
    );
  }

  /// Converts a corner's pixel drag into a new geographic edge position.
  void _dragCorner(_Corner corner, Offset delta, MapCamera camera) {
    final anchor = switch (corner) {
      _Corner.nw => bounds.nw,
      _Corner.ne => bounds.ne,
      _Corner.sw => bounds.sw,
      _Corner.se => bounds.se,
    };

    final p = camera.latLngToScreenPoint(anchor);
    final moved = camera.pointToLatLng(
      math.Point(p.x + delta.dx, p.y + delta.dy),
    );

    var north = bounds.north;
    var south = bounds.south;
    var east = bounds.east;
    var west = bounds.west;

    switch (corner) {
      case _Corner.nw:
        north = moved.latitude;
        west = moved.longitude;
      case _Corner.ne:
        north = moved.latitude;
        east = moved.longitude;
      case _Corner.sw:
        south = moved.latitude;
        west = moved.longitude;
      case _Corner.se:
        south = moved.latitude;
        east = moved.longitude;
    }

    // Keep the dragged edge from crossing its opposite edge.
    if (north - south < _minSep) {
      if (corner == _Corner.nw || corner == _Corner.ne) {
        north = south + _minSep;
      } else {
        south = north - _minSep;
      }
    }
    if (east - west < _minSep) {
      if (corner == _Corner.ne || corner == _Corner.se) {
        east = west + _minSep;
      } else {
        west = east - _minSep;
      }
    }

    onChanged(AreaBounds(north: north, south: south, east: east, west: west));
  }

  /// Translates the whole box by the pixel drag, in geographic space.
  void _dragMove(Offset delta, MapCamera camera) {
    final oldCenter = bounds.center;
    final p = camera.latLngToScreenPoint(oldCenter);
    final moved = camera.pointToLatLng(
      math.Point(p.x + delta.dx, p.y + delta.dy),
    );
    onChanged(
      bounds.translated(
        moved.latitude - oldCenter.latitude,
        moved.longitude - oldCenter.longitude,
      ),
    );
  }
}

class _RectPainter extends CustomPainter {
  final Offset nw, ne, se, sw;

  _RectPainter({required this.nw, required this.ne, required this.se, required this.sw});

  @override
  void paint(Canvas canvas, Size size) {
    final path = Path()
      ..moveTo(nw.dx, nw.dy)
      ..lineTo(ne.dx, ne.dy)
      ..lineTo(se.dx, se.dy)
      ..lineTo(sw.dx, sw.dy)
      ..close();

    canvas.drawPath(
      path,
      Paint()
        ..color = Colors.white.withValues(alpha: 0.12)
        ..style = PaintingStyle.fill,
    );
    canvas.drawPath(
      path,
      Paint()
        ..color = Colors.white
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2,
    );
  }

  @override
  bool shouldRepaint(covariant _RectPainter old) =>
      old.nw != nw || old.ne != ne || old.se != se || old.sw != sw;
}

class _HandleDot extends StatelessWidget {
  const _HandleDot();

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Container(
        width: 16,
        height: 16,
        decoration: BoxDecoration(
          color: Colors.white,
          shape: BoxShape.circle,
          border: Border.all(color: _accent, width: 2),
          boxShadow: [
            BoxShadow(color: Colors.black.withValues(alpha: 0.4), blurRadius: 4),
          ],
        ),
      ),
    );
  }
}

class _MoveDot extends StatelessWidget {
  const _MoveDot();

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Container(
        width: 34,
        height: 34,
        decoration: BoxDecoration(
          color: Colors.black.withValues(alpha: 0.55),
          shape: BoxShape.circle,
          border: Border.all(color: Colors.white, width: 1.5),
        ),
        child: const Icon(Icons.open_with, color: Colors.white, size: 18),
      ),
    );
  }
}

class _AreaBadge extends StatelessWidget {
  final double areaKm2;

  const _AreaBadge({required this.areaKm2});

  @override
  Widget build(BuildContext context) {
    final value = areaKm2 >= 100
        ? areaKm2.toStringAsFixed(0)
        : areaKm2.toStringAsFixed(areaKm2 >= 10 ? 1 : 2);

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      decoration: BoxDecoration(
        color: Colors.black.withValues(alpha: 0.75),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Text(
        '$value km²',
        style: const TextStyle(
          color: _accent,
          fontWeight: FontWeight.bold,
          fontSize: 18,
        ),
      ),
    );
  }
}
