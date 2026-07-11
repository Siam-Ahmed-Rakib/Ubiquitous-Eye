import 'dart:math' as math;

import 'package:flutter/gestures.dart';
import 'package:flutter/services.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_map/flutter_map.dart';

/// Interaction flags for a [FlutterMap] wrapped in [SmoothMapGestures].
///
/// Rotation is off everywhere in this app. `scrollWheelZoom` is off because
/// [SmoothMapGestures] replaces it — see that class for why.
const int kSmoothInteractiveFlags =
    InteractiveFlag.all & ~InteractiveFlag.rotate & ~InteractiveFlag.scrollWheelZoom;

/// Desktop- and touchpad-friendly zooming for the map it wraps.
///
/// flutter_map 6 zooms by `scrollDelta.dy * scrollWheelVelocity` with no upper
/// bound, and ignores [PointerScaleEvent] entirely. On a laptop that means a
/// two-finger scroll (which fires many small events per second) sends the map
/// flying, and a trackpad pinch does nothing at all. Here we
///
///  * clamp each scroll event and normalise a mouse wheel's coarse notches
///    against a touchpad's fine deltas, so both feel the same;
///  * zoom on [PointerScaleEvent], which is what the browser reports for a
///    trackpad pinch; and
///  * pan + zoom on trackpad pan/zoom gestures on desktop builds.
///
/// The map's own `scrollWheelZoom` flag must be off (use
/// [kSmoothInteractiveFlags]) or it claims the scroll events first: pointer
/// signals are offered to the innermost listener first, and only the first
/// registration with [PointerSignalResolver] runs.
class SmoothMapGestures extends StatefulWidget {
  final MapController controller;
  final double minZoom;
  final double maxZoom;

  /// Set false to ignore pointer signals, e.g. while a selection handle is
  /// being dragged.
  final bool enabled;

  final Widget child;

  const SmoothMapGestures({
    super.key,
    required this.controller,
    required this.child,
    this.minZoom = 2,
    this.maxZoom = 18,
    this.enabled = true,
  });

  @override
  State<SmoothMapGestures> createState() => _SmoothMapGesturesState();
}

class _SmoothMapGesturesState extends State<SmoothMapGestures> {
  /// Mouse wheels report one coarse notch (Chrome: ~100px); touchpads report a
  /// stream of small deltas. Anything at or above this is treated as a notch.
  static const double _wheelNotchThreshold = 45;

  /// Zoom levels per mouse-wheel notch.
  static const double _mouseWheelZoomStep = 0.45;

  /// Zoom levels per pixel of touchpad scroll, and the per-event cap that stops
  /// a fast flick from jumping several zoom levels at once.
  static const double _trackpadZoomPerPixel = 0.0035;
  static const double _trackpadMaxDelta = 60;

  /// Zoom levels per pixel of ctrl+wheel, which is how browsers report a
  /// trackpad pinch when they don't synthesise a [PointerScaleEvent].
  static const double _pinchZoomPerPixel = 0.010;

  double? _panZoomBaseZoom;

  MapCamera get _camera => widget.controller.camera;

  void _zoomTo(double zoom, Offset focalPoint) {
    final camera = _camera;
    final target = zoom.clamp(widget.minZoom, widget.maxZoom);
    if ((target - camera.zoom).abs() < 0.0005) return;

    // Keep the point under the cursor pinned while the zoom changes.
    final center = camera.focusedZoomCenter(
      math.Point(focalPoint.dx, focalPoint.dy),
      target,
    );
    widget.controller.move(center, target);
  }

  void _zoomBy(double delta, Offset focalPoint) =>
      _zoomTo(_camera.zoom + delta, focalPoint);

  void _panByPixels(Offset delta) {
    final camera = _camera;
    final origin = camera.latLngToScreenPoint(camera.center);
    final moved = camera.pointToLatLng(
      math.Point(origin.x + delta.dx, origin.y + delta.dy),
    );
    widget.controller.move(moved, camera.zoom);
  }

  /// Zoom levels for one scroll event. Scrolling down (`dy > 0`) zooms out.
  double _zoomDeltaForScroll(double dy, {required bool pinching}) {
    if (pinching) return -dy * _pinchZoomPerPixel;
    if (dy.abs() >= _wheelNotchThreshold) {
      return -dy.sign * _mouseWheelZoomStep;
    }
    return -dy.clamp(-_trackpadMaxDelta, _trackpadMaxDelta) * _trackpadZoomPerPixel;
  }

  void _onPointerSignal(PointerSignalEvent event) {
    if (!widget.enabled) return;

    if (event is PointerScrollEvent) {
      if (event.scrollDelta.dy == 0) return;
      GestureBinding.instance.pointerSignalResolver.register(event, (resolved) {
        resolved as PointerScrollEvent;
        // Browsers set the ctrl modifier on a wheel event to signal a pinch.
        final pinching = HardwareKeyboard.instance.isControlPressed ||
            HardwareKeyboard.instance.isMetaPressed;
        _zoomBy(
          _zoomDeltaForScroll(resolved.scrollDelta.dy, pinching: pinching),
          resolved.localPosition,
        );
      });
      return;
    }

    if (event is PointerScaleEvent) {
      if (event.scale <= 0 || event.scale == 1) return;
      GestureBinding.instance.pointerSignalResolver.register(event, (resolved) {
        resolved as PointerScaleEvent;
        _zoomBy(math.log(resolved.scale) / math.ln2, resolved.localPosition);
      });
    }
  }

  void _onPanZoomStart(PointerPanZoomStartEvent event) {
    _panZoomBaseZoom = _camera.zoom;
  }

  void _onPanZoomUpdate(PointerPanZoomUpdateEvent event) {
    if (!widget.enabled) return;

    // Content follows the fingers, so the camera centre moves the other way.
    if (event.panDelta != Offset.zero) _panByPixels(-event.panDelta);

    final base = _panZoomBaseZoom;
    if (base != null && event.scale > 0) {
      _zoomTo(base + math.log(event.scale) / math.ln2, event.localPosition);
    }
  }

  void _onPanZoomEnd(PointerPanZoomEndEvent event) => _panZoomBaseZoom = null;

  @override
  Widget build(BuildContext context) {
    return Listener(
      onPointerSignal: _onPointerSignal,
      onPointerPanZoomStart: _onPanZoomStart,
      onPointerPanZoomUpdate: _onPanZoomUpdate,
      onPointerPanZoomEnd: _onPanZoomEnd,
      child: widget.child,
    );
  }
}
