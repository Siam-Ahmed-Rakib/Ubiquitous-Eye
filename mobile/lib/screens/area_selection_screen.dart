import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:latlong2/latlong.dart';

import '../models/analytics_service.dart';
import '../models/area_bounds.dart';
import '../widgets/map_controls.dart';
import '../widgets/map_gestures.dart';
import '../widgets/map_zoom_controls.dart';
import '../widgets/search_panel.dart';
import 'analysis/analysis_run_screen.dart';
import 'analysis/land_use_screen.dart';

/// A pannable satellite map with a rectangular area-of-interest selector.
///
/// Used in two places: as the "New Image" tab root (no back button), and as
/// the "Select your area of interest" step in the Analytics flow (with a back
/// button). The bottom navigation bar is provided by the host scaffold, so it
/// is not part of this screen.
class AreaSelectionScreen extends StatefulWidget {
  final bool showBackButton;

  /// Optional analytics service this selection belongs to. Carried through to
  /// the analysis screen for titling/context; null for the "New Image" flow.
  final AnalyticsService? service;

  const AreaSelectionScreen({
    super.key,
    this.showBackButton = false,
    this.service,
  });

  @override
  State<AreaSelectionScreen> createState() => _AreaSelectionScreenState();
}

const Color _polygonAccent = Color(0xFFEF9A3D);

class _AreaSelectionScreenState extends State<AreaSelectionScreen> {
  // Esri free tile services give the labelled-satellite look from the mockup.
  static const String _imageryUrl =
      'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';
  static const String _labelsUrl =
      'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}';
  static const String _streetUrl =
      'https://tile.openstreetmap.org/{z}/{x}/{y}.png';

  static const LatLng _initialCenter = LatLng(23.7806, 90.3998); // Dhaka

  static const double _minZoom = 2;
  static const double _maxZoom = 18;

  final MapController _mapController = MapController();

  bool _satellite = true;

  /// The corners tapped so far. Four of them make the area.
  ///
  /// This is the only way to select an area. A draggable box came first and was
  /// removed: it needed a precise press on a small handle, which is awkward on
  /// a phone and worse one-handed, and it opened as a 5 km square sitting over
  /// the middle of the map whether or not that was anywhere near the area
  /// wanted — so the first action was always moving something nobody asked for.
  /// Tapping corners needs no precision, and nothing is drawn until the user
  /// draws it.
  final List<LatLng> _corners = [];

  static const int _requiredCorners = 4;

  bool get _polygonComplete => _corners.length == _requiredCorners;

  @override
  void dispose() {
    _mapController.dispose();
    super.dispose();
  }

  void _zoomBy(double delta) {
    final camera = _mapController.camera;
    _mapController.move(
      camera.center,
      (camera.zoom + delta).clamp(_minZoom, _maxZoom),
    );
  }

  void _onLocationSelected(LatLng location) {
    _mapController.move(location, 13);
    // Corners belong to the place they were tapped on. Jumping somewhere else
    // would otherwise leave them off-screen, still counting toward the four and
    // still defining the area, with nothing on screen to show it.
    setState(_corners.clear);
    FocusScope.of(context).unfocus();
  }

  void _resetSelection() => setState(_corners.clear);

  void _undoCorner() {
    if (_corners.isEmpty) return;
    setState(_corners.removeLast);
  }

  /// Drops a corner. Ignored once four are down — [_undoCorner] or reset first,
  /// so a stray tap on a finished area cannot silently change it.
  void _onMapTap(LatLng point) {
    if (_polygonComplete) return;
    setState(() => _corners.add(point));
  }

  void _continue() {
    final bounds = AreaBounds.fromPoints(_corners);
    // Land Use Classification labels a single date; every other service (and
    // the generic "New Image" flow) compares two dates for change.
    final service = widget.service;
    Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => service?.id == 'land_use_classification'
            ? LandUseScreen(bounds: bounds, service: service)
            : AnalysisRunScreen(bounds: bounds, service: service),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      resizeToAvoidBottomInset: false,
      body: Stack(
        children: [
          Positioned.fill(child: _buildMap()),
          _buildTopBar(),
          _buildZoomControls(),
          _buildPolygonBar(),
          _buildContinueButton(),
        ],
      ),
    );
  }

  Widget _buildMap() {
    return SmoothMapGestures(
      controller: _mapController,
      minZoom: _minZoom,
      maxZoom: _maxZoom,
      child: FlutterMap(
        mapController: _mapController,
        options: MapOptions(
          initialCenter: _initialCenter,
          initialZoom: 12.5,
          minZoom: _minZoom,
          maxZoom: _maxZoom,
          backgroundColor: const Color(0xFF1A1A1A),
        onTap: (_, point) => _onMapTap(point),
        // Left at the defaults. Clearing `InteractiveFlag.doubleTapZoom` to
        // stop a fast pair of corner taps being read as a double tap looks
        // reasonable and is not: in flutter_map 6 it stops `onTap` firing at
        // all, so no corner is ever placed. Flutter's own double-tap slop
        // (~100 logical pixels) already means two corners far enough apart to
        // be worth tapping are not a double tap, and Undo covers the rest.
        interactionOptions:
            const InteractionOptions(flags: kSmoothInteractiveFlags),
      ),
      children: [
        TileLayer(
          urlTemplate: _satellite ? _imageryUrl : _streetUrl,
          userAgentPackageName: 'com.ubiquitouseye.app',
          maxNativeZoom: 18,
        ),
        if (_satellite)
          TileLayer(
            urlTemplate: _labelsUrl,
            userAgentPackageName: 'com.ubiquitouseye.app',
            maxNativeZoom: 18,
          ),
        ..._buildPolygonLayers(),
        _buildAttribution(),
      ],
      ),
    );
  }

  /// The tapped corners, the outline they make, and the area that will actually
  /// be analysed.
  ///
  /// The enclosing box is drawn as well as the quadrilateral, because they are
  /// not the same shape unless the taps happen to be square — and the box is
  /// what the backend uses. Showing only the quadrilateral would promise an
  /// area the analysis does not deliver.
  List<Widget> _buildPolygonLayers() {
    // No corners, no layers at all — not even empty ones. The map is meant to
    // open with nothing drawn over it.
    if (_corners.isEmpty) return const [];
    final enclosing = AreaBounds.fromPoints(_corners);
    return [
      PolygonLayer(
        polygons: [
          Polygon(
            points: [
              enclosing.nw,
              enclosing.ne,
              enclosing.se,
              enclosing.sw,
            ],
            color: _polygonAccent.withValues(alpha: 0.12),
            borderColor: _polygonAccent,
            borderStrokeWidth: 2,
          ),
        ],
      ),
      if (_corners.length >= 2)
        PolylineLayer(
          polylines: [
            Polyline(
              points: _polygonComplete
                  ? [..._corners, _corners.first]
                  : _corners,
              color: Colors.white,
              strokeWidth: 2,
            ),
          ],
        ),
      MarkerLayer(
        markers: [
          for (var i = 0; i < _corners.length; i++)
            Marker(
              point: _corners[i],
              width: 30,
              height: 30,
              child: _CornerDot(index: i + 1),
            ),
        ],
      ),
    ];
  }

  /// Undo / reset, plus a line saying what to do. Sits above the Continue
  /// button, thumb-height on a phone rather than tucked into the top bar.
  Widget _buildPolygonBar() {
    return Positioned(
      left: 20,
      right: 20,
      bottom: 84,
      child: Align(
        alignment: Alignment.center,
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 460),
          child: Container(
            padding: const EdgeInsets.fromLTRB(14, 8, 8, 8),
            decoration: BoxDecoration(
              color: Colors.white,
              borderRadius: BorderRadius.circular(14),
              boxShadow: [
                BoxShadow(
                  color: Colors.black.withValues(alpha: 0.2),
                  blurRadius: 8,
                  offset: const Offset(0, 2),
                ),
              ],
            ),
            // Labelled buttons where they fit, icons alone where they do not.
            // A 320 px phone at 1.3x text scale cannot hold the instruction and
            // two worded buttons on one line, and the instruction is the part
            // that has to stay readable.
            child: LayoutBuilder(
              builder: (context, constraints) {
                final compact = constraints.maxWidth < 360 ||
                    MediaQuery.textScalerOf(context).scale(13) > 15;
                return Row(
                  children: [
                    Expanded(
                      child: Text(
                        _polygonComplete
                            ? 'Area set from 4 corners'
                            : 'Tap the map to drop corner '
                                '${_corners.length + 1} of $_requiredCorners',
                        style: const TextStyle(
                          fontSize: 13,
                          fontWeight: FontWeight.w600,
                          color: Colors.black87,
                        ),
                      ),
                    ),
                    _polygonAction(
                      key: const Key('polygon-undo'),
                      icon: Icons.undo,
                      label: 'Undo',
                      compact: compact,
                      onPressed: _corners.isEmpty ? null : _undoCorner,
                    ),
                    _polygonAction(
                      key: const Key('polygon-reset'),
                      icon: Icons.restart_alt,
                      label: 'Reset',
                      compact: compact,
                      onPressed: _corners.isEmpty ? null : _resetSelection,
                    ),
                  ],
                );
              },
            ),
          ),
        ),
      ),
    );
  }

  /// Undo / Reset, worded when there is room and icon-only when there is not.
  Widget _polygonAction({
    required Key key,
    required IconData icon,
    required String label,
    required bool compact,
    required VoidCallback? onPressed,
  }) {
    if (compact) {
      return IconButton(
        key: key,
        onPressed: onPressed,
        tooltip: label,
        visualDensity: VisualDensity.compact,
        icon: Icon(icon, size: 20),
        color: Colors.black87,
      );
    }
    return TextButton.icon(
      key: key,
      onPressed: onPressed,
      icon: Icon(icon, size: 18),
      label: Text(label),
      style: TextButton.styleFrom(foregroundColor: Colors.black87),
    );
  }

  Widget _buildZoomControls() {
    return Positioned(
      right: 16,
      bottom: 88,
      child: MapZoomControls(
        onZoomIn: () => _zoomBy(1),
        onZoomOut: () => _zoomBy(-1),
      ),
    );
  }

  Widget _buildAttribution() {
    return IgnorePointer(
      child: Align(
        alignment: Alignment.bottomLeft,
        child: Padding(
          padding: const EdgeInsets.only(left: 6, bottom: 84),
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
            color: Colors.black26,
            child: Text(
              _satellite ? '© Esri, Maxar' : '© OpenStreetMap',
              style: const TextStyle(color: Colors.white70, fontSize: 10),
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildTopBar() {
    return Positioned(
      top: 0,
      left: 0,
      right: 0,
      child: SafeArea(
        bottom: false,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(12, 8, 12, 0),
          // Keep the search cluster from stretching across wide windows.
          child: Align(
            alignment: Alignment.topCenter,
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 760),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  if (widget.showBackButton) ...[
                    _backButton(),
                    const SizedBox(width: 8),
                  ],
                  Expanded(
                    child: SearchPanel(
                      onLocationSelected: _onLocationSelected,
                      trailing: MapControls(
                        satellite: _satellite,
                        onToggleLayers: () =>
                            setState(() => _satellite = !_satellite),
                        onResetSelection: _resetSelection,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  Widget _backButton() {
    return Container(
      height: 52,
      width: 52,
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withValues(alpha: 0.2),
            blurRadius: 8,
            offset: const Offset(0, 2),
          ),
        ],
      ),
      child: Material(
        color: Colors.transparent,
        child: InkWell(
          borderRadius: BorderRadius.circular(12),
          onTap: () => Navigator.of(context).maybePop(),
          child: const Icon(Icons.arrow_back, color: Colors.black87),
        ),
      ),
    );
  }

  Widget _buildContinueButton() {
    return Positioned(
      left: 20,
      right: 20,
      bottom: 16,
      child: Align(
        alignment: Alignment.center,
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 460),
          child: SizedBox(
            height: 56,
            width: double.infinity,
            child: ElevatedButton(
              onPressed: _polygonComplete ? _continue : null,
              style: ElevatedButton.styleFrom(
                backgroundColor: Colors.white,
                foregroundColor: Colors.black,
                // Material's default disabled fill is near-black, which on this
                // black map is an invisible button — and it is disabled exactly
                // when it carries the instruction telling you what to do next.
                disabledBackgroundColor: Colors.white24,
                disabledForegroundColor: Colors.white70,
                elevation: 6,
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(14),
                ),
              ),
              child: Text(
                _polygonComplete
                    ? 'CONTINUE TO OPTIONS'
                    : 'TAP ${_requiredCorners - _corners.length} MORE CORNER'
                        '${_requiredCorners - _corners.length == 1 ? '' : 'S'}',
                style: const TextStyle(
                  fontSize: 16,
                  fontWeight: FontWeight.bold,
                  letterSpacing: 0.5,
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// A numbered corner marker. Numbered because the order is what Undo removes,
/// so the user can see which tap goes next.
class _CornerDot extends StatelessWidget {
  final int index;

  const _CornerDot({required this.index});

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Container(
        width: 24,
        height: 24,
        decoration: BoxDecoration(
          color: _polygonAccent,
          shape: BoxShape.circle,
          border: Border.all(color: Colors.white, width: 2),
          boxShadow: [
            BoxShadow(
              color: Colors.black.withValues(alpha: 0.35),
              blurRadius: 4,
            ),
          ],
        ),
        alignment: Alignment.center,
        child: Text(
          '$index',
          style: const TextStyle(
            fontSize: 12,
            fontWeight: FontWeight.bold,
            color: Colors.white,
          ),
        ),
      ),
    );
  }
}
