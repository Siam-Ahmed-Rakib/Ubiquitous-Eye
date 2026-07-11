import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:latlong2/latlong.dart';

import '../models/analytics_service.dart';
import '../models/area_bounds.dart';
import '../widgets/map_controls.dart';
import '../widgets/map_gestures.dart';
import '../widgets/map_zoom_controls.dart';
import '../widgets/search_panel.dart';
import '../widgets/selection_overlay.dart';
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

  late AreaBounds _bounds;
  bool _draggingHandle = false;
  bool _satellite = true;

  /// When locked the selection box ignores pointers, so a drag anywhere pans
  /// the map instead of moving the box.
  bool _boxLocked = false;

  @override
  void initState() {
    super.initState();
    _bounds = AreaBounds.square(center: _initialCenter, sizeKm: 5);
  }

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
    setState(() => _bounds = AreaBounds.square(center: location, sizeKm: 5));
    FocusScope.of(context).unfocus();
  }

  void _resetSelection() {
    setState(
      () => _bounds =
          AreaBounds.square(center: _mapController.camera.center, sizeKm: 5),
    );
  }

  void _continue() {
    // Land Use Classification labels a single date; every other service (and
    // the generic "New Image" flow) compares two dates for change.
    final service = widget.service;
    Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => service?.id == 'land_use_classification'
            ? LandUseScreen(bounds: _bounds, service: service)
            : AnalysisRunScreen(bounds: _bounds, service: service),
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
      enabled: !_draggingHandle,
      child: FlutterMap(
        mapController: _mapController,
        options: MapOptions(
          initialCenter: _initialCenter,
          initialZoom: 12.5,
          minZoom: _minZoom,
          maxZoom: _maxZoom,
          backgroundColor: const Color(0xFF1A1A1A),
          // Freeze map gestures while a selection handle is being dragged.
          interactionOptions: InteractionOptions(
            flags: _draggingHandle ? InteractiveFlag.none : kSmoothInteractiveFlags,
          ),
        ),
        children: [
          TileLayer(
            urlTemplate: _satellite ? _imageryUrl : _streetUrl,
            userAgentPackageName: 'com.terrascope.app',
            maxNativeZoom: 18,
          ),
          if (_satellite)
            TileLayer(
              urlTemplate: _labelsUrl,
              userAgentPackageName: 'com.terrascope.app',
              maxNativeZoom: 18,
            ),
          AreaSelectionOverlay(
            bounds: _bounds,
            locked: _boxLocked,
            onChanged: (b) => setState(() => _bounds = b),
            onDragStart: () => setState(() => _draggingHandle = true),
            onDragEnd: () => setState(() => _draggingHandle = false),
          ),
          _buildAttribution(),
        ],
      ),
    );
  }

  Widget _buildZoomControls() {
    return Positioned(
      right: 16,
      bottom: 88,
      child: MapZoomControls(
        onZoomIn: () => _zoomBy(1),
        onZoomOut: () => _zoomBy(-1),
        boxLocked: _boxLocked,
        onToggleBoxLock: () => setState(() => _boxLocked = !_boxLocked),
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
              onPressed: _continue,
              style: ElevatedButton.styleFrom(
                backgroundColor: Colors.white,
                foregroundColor: Colors.black,
                elevation: 6,
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(14),
                ),
              ),
              child: const Text(
                'CONTINUE TO OPTIONS',
                style: TextStyle(
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
