import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
// latlong2 exports its own `Path` type; hide it so `Path` below means dart:ui.Path.
import 'package:latlong2/latlong.dart' hide Path;

import '../models/area_bounds.dart';

enum _Corner { nw, ne, sw, se }

enum _Edge { n, e, s, w }

const Color _accent = Color(0xFFEF9A3D);

/// A flutter_map child layer that draws the selection rectangle and its
/// interactive handles.
///
/// Because it reads [MapCamera.of] it rebuilds on every pan/zoom, so the box
/// stays anchored to the geography.
///
/// Dragging anywhere inside the box moves it; the corner and edge handles
/// resize it. Hit targets are deliberately larger than the dots they draw —
/// a laptop touchpad is far less precise than a fingertip. Each drag reports
/// start/end so the parent can freeze map gestures while a handle is in use.
///
/// While [locked] the box is inert and shows a lock badge, so the map can be
/// panned by dragging straight through it.
class AreaSelectionOverlay extends StatelessWidget {
  final AreaBounds bounds;
  final ValueChanged<AreaBounds> onChanged;
  final VoidCallback onDragStart;
  final VoidCallback onDragEnd;
  final bool locked;

  const AreaSelectionOverlay({
    super.key,
    required this.bounds,
    required this.onChanged,
    required this.onDragStart,
    required this.onDragEnd,
    this.locked = false,
  });

  // Visible dot sizes, and the (larger) square each one accepts a drag within.
  static const double _cornerDot = 18;
  static const double _cornerHit = 44;
  static const double _edgeBarLong = 26;
  static const double _edgeBarShort = 6;
  static const double _edgeHitLong = 48;
  static const double _edgeHitShort = 30;
  static const double _centreDot = 34;
  static const double _centreHit = 52;

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

        // Drag anywhere in the box to move it. Sits below the handles so they
        // win the hit test where they overlap.
        if (!locked) _interior(nw, se, camera),

        // Area badge straddling the top edge.
        Positioned(
          left: topCenter.dx,
          top: topCenter.dy,
          child: FractionalTranslation(
            translation: const Offset(-0.5, -0.5),
            child: IgnorePointer(child: _AreaBadge(areaKm2: bounds.areaKm2)),
          ),
        ),

        if (locked)
          _centred(center, _centreDot, const IgnorePointer(child: _LockDot()))
        else ...[
          _edgeHandle(_Edge.n, toOffset(bounds.topCenter), camera),
          _edgeHandle(_Edge.s, toOffset(bounds.bottomCenter), camera),
          _edgeHandle(_Edge.w, toOffset(bounds.leftCenter), camera),
          _edgeHandle(_Edge.e, toOffset(bounds.rightCenter), camera),
          _cornerHandle(_Corner.nw, nw, camera),
          _cornerHandle(_Corner.ne, ne, camera),
          _cornerHandle(_Corner.sw, sw, camera),
          _cornerHandle(_Corner.se, se, camera),
          _moveHandle(center, camera),
        ],
      ],
    );
  }

  /// Positions [child] of size [size] centred on [pos].
  Widget _centred(Offset pos, double size, Widget child) => Positioned(
        left: pos.dx - size / 2,
        top: pos.dy - size / 2,
        width: size,
        height: size,
        child: child,
      );

  Widget _interior(Offset nw, Offset se, MapCamera camera) {
    final left = math.min(nw.dx, se.dx);
    final top = math.min(nw.dy, se.dy);
    final width = (se.dx - nw.dx).abs();
    final height = (se.dy - nw.dy).abs();

    return Positioned(
      left: left,
      top: top,
      width: width,
      height: height,
      child: MouseRegion(
        cursor: SystemMouseCursors.move,
        child: GestureDetector(
          behavior: HitTestBehavior.opaque,
          onPanStart: (_) => onDragStart(),
          onPanUpdate: (d) => _dragMove(d.delta, camera),
          onPanEnd: (_) => onDragEnd(),
          onPanCancel: onDragEnd,
        ),
      ),
    );
  }

  Widget _cornerHandle(_Corner corner, Offset pos, MapCamera camera) {
    final cursor = switch (corner) {
      _Corner.nw || _Corner.se => SystemMouseCursors.resizeUpLeftDownRight,
      _Corner.ne || _Corner.sw => SystemMouseCursors.resizeUpRightDownLeft,
    };

    return _centred(
      pos,
      _cornerHit,
      MouseRegion(
        cursor: cursor,
        child: GestureDetector(
          behavior: HitTestBehavior.opaque,
          onPanStart: (_) => onDragStart(),
          onPanUpdate: (d) => _dragCorner(corner, d.delta, camera),
          onPanEnd: (_) => onDragEnd(),
          onPanCancel: onDragEnd,
          child: const Center(child: _HandleDot(size: _cornerDot)),
        ),
      ),
    );
  }

  Widget _edgeHandle(_Edge edge, Offset pos, MapCamera camera) {
    final horizontal = edge == _Edge.n || edge == _Edge.s;
    final hitWidth = horizontal ? _edgeHitLong : _edgeHitShort;
    final hitHeight = horizontal ? _edgeHitShort : _edgeHitLong;

    return Positioned(
      left: pos.dx - hitWidth / 2,
      top: pos.dy - hitHeight / 2,
      width: hitWidth,
      height: hitHeight,
      child: MouseRegion(
        cursor: horizontal
            ? SystemMouseCursors.resizeUpDown
            : SystemMouseCursors.resizeLeftRight,
        child: GestureDetector(
          behavior: HitTestBehavior.opaque,
          onPanStart: (_) => onDragStart(),
          onPanUpdate: (d) => _dragEdge(edge, d.delta, camera),
          onPanEnd: (_) => onDragEnd(),
          onPanCancel: onDragEnd,
          child: Center(
            child: _EdgeBar(
              width: horizontal ? _edgeBarLong : _edgeBarShort,
              height: horizontal ? _edgeBarShort : _edgeBarLong,
            ),
          ),
        ),
      ),
    );
  }

  Widget _moveHandle(Offset pos, MapCamera camera) {
    return _centred(
      pos,
      _centreHit,
      MouseRegion(
        cursor: SystemMouseCursors.move,
        child: GestureDetector(
          behavior: HitTestBehavior.opaque,
          onPanStart: (_) => onDragStart(),
          onPanUpdate: (d) => _dragMove(d.delta, camera),
          onPanEnd: (_) => onDragEnd(),
          onPanCancel: onDragEnd,
          child: const Center(child: _MoveDot()),
        ),
      ),
    );
  }

  /// Maps a pixel drag of [anchor] onto the geographic point it lands on.
  LatLng _dragged(LatLng anchor, Offset delta, MapCamera camera) {
    final p = camera.latLngToScreenPoint(anchor);
    return camera.pointToLatLng(math.Point(p.x + delta.dx, p.y + delta.dy));
  }

  /// Converts a corner's pixel drag into a new geographic edge position.
  void _dragCorner(_Corner corner, Offset delta, MapCamera camera) {
    final anchor = switch (corner) {
      _Corner.nw => bounds.nw,
      _Corner.ne => bounds.ne,
      _Corner.sw => bounds.sw,
      _Corner.se => bounds.se,
    };
    final moved = _dragged(anchor, delta, camera);

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

  /// Moves a single edge, leaving the other three where they are.
  void _dragEdge(_Edge edge, Offset delta, MapCamera camera) {
    final anchor = switch (edge) {
      _Edge.n => bounds.topCenter,
      _Edge.s => bounds.bottomCenter,
      _Edge.w => bounds.leftCenter,
      _Edge.e => bounds.rightCenter,
    };
    final moved = _dragged(anchor, delta, camera);

    var north = bounds.north;
    var south = bounds.south;
    var east = bounds.east;
    var west = bounds.west;

    switch (edge) {
      case _Edge.n:
        north = math.max(moved.latitude, south + _minSep);
      case _Edge.s:
        south = math.min(moved.latitude, north - _minSep);
      case _Edge.e:
        east = math.max(moved.longitude, west + _minSep);
      case _Edge.w:
        west = math.min(moved.longitude, east - _minSep);
    }

    onChanged(AreaBounds(north: north, south: south, east: east, west: west));
  }

  /// Translates the whole box by the pixel drag, in geographic space.
  void _dragMove(Offset delta, MapCamera camera) {
    final oldCenter = bounds.center;
    final moved = _dragged(oldCenter, delta, camera);
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
  final double size;

  const _HandleDot({required this.size});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        color: Colors.white,
        shape: BoxShape.circle,
        border: Border.all(color: _accent, width: 2),
        boxShadow: [
          BoxShadow(color: Colors.black.withValues(alpha: 0.4), blurRadius: 4),
        ],
      ),
    );
  }
}

class _EdgeBar extends StatelessWidget {
  final double width;
  final double height;

  const _EdgeBar({required this.width, required this.height});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: width,
      height: height,
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(3),
        border: Border.all(color: _accent, width: 1.5),
        boxShadow: [
          BoxShadow(color: Colors.black.withValues(alpha: 0.35), blurRadius: 3),
        ],
      ),
    );
  }
}

class _MoveDot extends StatelessWidget {
  const _MoveDot();

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 34,
      height: 34,
      decoration: BoxDecoration(
        color: Colors.black.withValues(alpha: 0.55),
        shape: BoxShape.circle,
        border: Border.all(color: Colors.white, width: 1.5),
      ),
      child: const Icon(Icons.open_with, color: Colors.white, size: 18),
    );
  }
}

class _LockDot extends StatelessWidget {
  const _LockDot();

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 34,
      height: 34,
      decoration: BoxDecoration(
        color: Colors.black.withValues(alpha: 0.55),
        shape: BoxShape.circle,
        border: Border.all(color: Colors.white70, width: 1.5),
      ),
      child: const Icon(Icons.lock_outline, color: Colors.white70, size: 18),
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
