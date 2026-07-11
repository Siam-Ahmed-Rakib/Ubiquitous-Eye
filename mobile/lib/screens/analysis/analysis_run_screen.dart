import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';

import '../../models/analytics_service.dart';
import '../../models/analysis_result.dart';
import '../../models/area_bounds.dart';
import '../../services/analysis_service.dart';
import '../../util/responsive.dart';
import '../../widgets/map_gestures.dart';
import '../../widgets/map_zoom_controls.dart';
import '../../widgets/month_year_field.dart';

const Color _ink = Color(0xFF0E1116);
const Color _accent = Color(0xFFEF9A3D);
const Color _deforestColor = Color(0xFFE53935); // mask 1 — vegetation/forest loss
const Color _waterColor = Color(0xFFFB8C00); // mask 2 — surface-water loss

/// Runs the backend change-detection pipeline over a selected area and shows
/// the result: changed pixels drawn on the map (red = deforestation,
/// orange = water loss) plus summary statistics.
///
/// Reached from the "New Image" flow (generic) and from the Analytics flow
/// (with a [service] for context/title).
class AnalysisRunScreen extends StatefulWidget {
  final AreaBounds bounds;
  final AnalyticsService? service;

  const AnalysisRunScreen({super.key, required this.bounds, this.service});

  @override
  State<AnalysisRunScreen> createState() => _AnalysisRunScreenState();
}

class _AnalysisRunScreenState extends State<AnalysisRunScreen> {
  static const String _imageryUrl =
      'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';
  static const String _labelsUrl =
      'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}';

  static const int _maxRenderPoints = 6000;

  static const double _minZoom = 2;
  static const double _maxZoom = 18;

  final AnalysisService _service = AnalysisService();
  final MapController _mapController = MapController();

  late int _oldYear;
  late int _oldMonth;
  late int _newYear;
  late int _newMonth;

  bool _loading = false;
  String? _error;
  AnalysisResult? _result;

  @override
  void initState() {
    super.initState();
    final now = DateTime.now();
    _oldYear = now.year - 1;
    _oldMonth = 1;
    _newYear = now.year;
    _newMonth = now.month;
  }

  @override
  void dispose() {
    _service.dispose();
    _mapController.dispose();
    super.dispose();
  }

  Future<void> _run({bool sample = false}) async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final result = sample
          ? sampleAnalysis(
              bounds: widget.bounds,
              oldYear: _oldYear,
              oldMonth: _oldMonth,
              newYear: _newYear,
              newMonth: _newMonth,
            )
          : await _service.analyze(
              bounds: widget.bounds,
              oldYear: _oldYear,
              oldMonth: _oldMonth,
              newYear: _newYear,
              newMonth: _newMonth,
            );
      if (!mounted) return;
      setState(() {
        _result = result;
        _loading = false;
      });
    } on AnalysisException catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.message;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = 'Unexpected error: $e';
        _loading = false;
      });
    }
  }

  void _reset() => setState(() {
        _result = null;
        _error = null;
      });

  void _zoomBy(double delta) {
    final camera = _mapController.camera;
    _mapController.move(
      camera.center,
      (camera.zoom + delta).clamp(_minZoom, _maxZoom),
    );
  }

  @override
  Widget build(BuildContext context) {
    final title = widget.service?.name ?? 'Change Analysis';
    return Scaffold(
      backgroundColor: const Color(0xFFF3F4F6),
      appBar: AppBar(
        backgroundColor: Colors.white,
        surfaceTintColor: Colors.white,
        foregroundColor: _ink,
        elevation: 0.5,
        title: Text(
          title,
          style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 18),
        ),
      ),
      body: Column(
        children: [
          Expanded(child: _buildMap()),
          _buildPanel(context),
        ],
      ),
    );
  }

  // ── Map ──────────────────────────────────────────────────────────────────
  Widget _buildMap() {
    final b = widget.bounds;
    return Stack(
      children: [
        Positioned.fill(
          child: SmoothMapGestures(
            controller: _mapController,
            minZoom: _minZoom,
            maxZoom: _maxZoom,
            child: FlutterMap(
              mapController: _mapController,
              options: MapOptions(
                initialCameraFit: CameraFit.bounds(
                  bounds: LatLngBounds(b.nw, b.se),
                  padding: const EdgeInsets.all(48),
                ),
                minZoom: _minZoom,
                maxZoom: _maxZoom,
                backgroundColor: const Color(0xFF1A1A1A),
                interactionOptions:
                    const InteractionOptions(flags: kSmoothInteractiveFlags),
              ),
              children: [
                TileLayer(
                  urlTemplate: _imageryUrl,
                  userAgentPackageName: 'com.terrascope.app',
                  maxNativeZoom: 18,
                ),
                TileLayer(
                  urlTemplate: _labelsUrl,
                  userAgentPackageName: 'com.terrascope.app',
                  maxNativeZoom: 18,
                ),
                PolygonLayer(
                  polygons: [
                    Polygon(
                      points: [b.nw, b.ne, b.se, b.sw],
                      isFilled: true,
                      color: Colors.white.withValues(alpha: 0.10),
                      borderColor: Colors.white,
                      borderStrokeWidth: 2,
                    ),
                  ],
                ),
                if (_result != null && _result!.hasChanges)
                  CircleLayer(circles: _changeMarkers(_result!.changes)),
              ],
            ),
          ),
        ),
        if (_result != null && _result!.hasChanges) _buildLegend(),
        Positioned(
          right: 16,
          bottom: 16,
          child: MapZoomControls(
            onZoomIn: () => _zoomBy(1),
            onZoomOut: () => _zoomBy(-1),
          ),
        ),
        if (_loading) _buildLoadingOverlay(),
      ],
    );
  }

  List<CircleMarker> _changeMarkers(List<ChangePoint> pts) {
    // Downsample for rendering if the backend returned a very large set; the
    // headline counts still come from the full `stats`.
    final step = pts.length > _maxRenderPoints
        ? (pts.length / _maxRenderPoints).ceil()
        : 1;
    final markers = <CircleMarker>[];
    for (var i = 0; i < pts.length; i += step) {
      final p = pts[i];
      final base = p.isDeforestation ? _deforestColor : _waterColor;
      markers.add(
        CircleMarker(
          point: p.location,
          radius: 3,
          color: base.withValues(alpha: 0.75),
          borderColor: base,
          borderStrokeWidth: 0.5,
        ),
      );
    }
    return markers;
  }

  Widget _buildLegend() {
    return Positioned(
      top: 12,
      right: 12,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        decoration: BoxDecoration(
          color: Colors.black.withValues(alpha: 0.72),
          borderRadius: BorderRadius.circular(10),
        ),
        child: const Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _LegendRow(color: _deforestColor, label: 'Deforestation'),
            SizedBox(height: 6),
            _LegendRow(color: _waterColor, label: 'Water loss'),
          ],
        ),
      ),
    );
  }

  Widget _buildLoadingOverlay() {
    return const Positioned.fill(
      child: ColoredBox(
        color: Colors.black54,
        child: Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              SizedBox(
                width: 44,
                height: 44,
                child: CircularProgressIndicator(color: _accent, strokeWidth: 3),
              ),
              SizedBox(height: 16),
              Text(
                'Running analysis…',
                style: TextStyle(
                  color: Colors.white,
                  fontSize: 16,
                  fontWeight: FontWeight.w600,
                ),
              ),
              SizedBox(height: 6),
              Text(
                'Fetching Sentinel-2 composites — this can take a\nfew minutes.',
                textAlign: TextAlign.center,
                style: TextStyle(color: Colors.white70, fontSize: 13),
              ),
            ],
          ),
        ),
      ),
    );
  }

  // ── Bottom panel ─────────────────────────────────────────────────────────
  Widget _buildPanel(BuildContext context) {
    final maxPanelHeight = context.screenHeight * 0.5;
    return Material(
      color: Colors.white,
      elevation: 12,
      child: SafeArea(
        top: false,
        child: ConstrainedBox(
          constraints: BoxConstraints(maxHeight: maxPanelHeight),
          child: SingleChildScrollView(
            child: ResponsiveCenter(
              maxWidth: 720,
              padding: const EdgeInsets.fromLTRB(20, 16, 20, 16),
              child: _result != null
                  ? _buildResults(_result!)
                  : _buildControls(),
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildControls() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            const Icon(Icons.crop_free, size: 18, color: _ink),
            const SizedBox(width: 8),
            Text(
              '${widget.bounds.areaKm2.toStringAsFixed(2)} km² selected',
              style: const TextStyle(fontWeight: FontWeight.w700, color: _ink),
            ),
          ],
        ),
        const SizedBox(height: 4),
        Text(
          'Compare two dates to detect change across your area of interest.',
          style: TextStyle(fontSize: 13, color: Colors.grey.shade600),
        ),
        const SizedBox(height: 16),
        MonthYearField(
          label: 'From (older date)',
          year: _oldYear,
          month: _oldMonth,
          onYear: (y) => setState(() => _oldYear = y),
          onMonth: (m) => setState(() => _oldMonth = m),
        ),
        const SizedBox(height: 12),
        MonthYearField(
          label: 'To (newer date)',
          year: _newYear,
          month: _newMonth,
          onYear: (y) => setState(() => _newYear = y),
          onMonth: (m) => setState(() => _newMonth = m),
        ),
        if (_error != null) ...[
          const SizedBox(height: 14),
          _ErrorBox(message: _error!),
        ],
        const SizedBox(height: 18),
        SizedBox(
          height: 52,
          child: ElevatedButton.icon(
            onPressed: _loading ? null : () => _run(),
            style: ElevatedButton.styleFrom(
              backgroundColor: _ink,
              foregroundColor: Colors.white,
              elevation: 0,
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(12),
              ),
            ),
            icon: const Icon(Icons.analytics_outlined, size: 20),
            label: const Text(
              'RUN ANALYSIS',
              style: TextStyle(
                fontSize: 15,
                fontWeight: FontWeight.w700,
                letterSpacing: 0.5,
              ),
            ),
          ),
        ),
        const SizedBox(height: 8),
        TextButton.icon(
          onPressed: _loading ? null : () => _run(sample: true),
          style: TextButton.styleFrom(foregroundColor: Colors.grey.shade700),
          icon: const Icon(Icons.science_outlined, size: 18),
          label: const Text('Load sample result (no backend needed)'),
        ),
      ],
    );
  }

  Widget _buildResults(AnalysisResult result) {
    final stats = result.stats;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (result.isSample) ...[
          _SampleBanner(),
          const SizedBox(height: 12),
        ],
        Row(
          children: [
            const Icon(Icons.insights_outlined, size: 20, color: _ink),
            const SizedBox(width: 8),
            const Expanded(
              child: Text(
                'Results',
                style: TextStyle(
                  fontSize: 18,
                  fontWeight: FontWeight.w800,
                  color: _ink,
                ),
              ),
            ),
            if (stats != null)
              Text(
                '${stats.oldDate}  →  ${stats.newDate}',
                style: TextStyle(fontSize: 12, color: Colors.grey.shade600),
              ),
          ],
        ),
        const SizedBox(height: 14),
        Row(
          children: [
            Expanded(
              child: _StatCard(
                color: _deforestColor,
                value: _formatCount(result.deforestationCount),
                label: 'Deforestation\npixels',
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: _StatCard(
                color: _waterColor,
                value: _formatCount(result.waterLossCount),
                label: 'Water-loss\npixels',
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: _StatCard(
                color: _ink,
                value: _formatCount(stats?.totalPixels ?? result.changes.length),
                label: 'Compared\npixels',
              ),
            ),
          ],
        ),
        const SizedBox(height: 14),
        Text(
          result.hasChanges
              ? 'Detected ${_formatCount(result.changes.length)} changed pixels, '
                  'shown on the map above.'
              : 'No change detected between the two dates.',
          style: TextStyle(fontSize: 13, color: Colors.grey.shade700),
        ),
        const SizedBox(height: 16),
        SizedBox(
          height: 48,
          child: OutlinedButton.icon(
            onPressed: _reset,
            style: OutlinedButton.styleFrom(
              foregroundColor: _ink,
              side: const BorderSide(color: _ink, width: 1.3),
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(12),
              ),
            ),
            icon: const Icon(Icons.tune, size: 18),
            label: const Text(
              'ADJUST DATES & RUN AGAIN',
              style: TextStyle(fontWeight: FontWeight.w700, letterSpacing: 0.3),
            ),
          ),
        ),
      ],
    );
  }

  static String _formatCount(int n) {
    if (n < 1000) return '$n';
    if (n < 1000000) return '${(n / 1000).toStringAsFixed(n < 10000 ? 1 : 0)}k';
    return '${(n / 1000000).toStringAsFixed(1)}M';
  }
}

// ── Small presentational widgets ────────────────────────────────────────────

class _LegendRow extends StatelessWidget {
  final Color color;
  final String label;
  const _LegendRow({required this.color, required this.label});

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: 12,
          height: 12,
          decoration: BoxDecoration(color: color, shape: BoxShape.circle),
        ),
        const SizedBox(width: 8),
        Text(
          label,
          style: const TextStyle(color: Colors.white, fontSize: 12),
        ),
      ],
    );
  }
}

class _StatCard extends StatelessWidget {
  final Color color;
  final String value;
  final String label;
  const _StatCard({
    required this.color,
    required this.value,
    required this.label,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(vertical: 14, horizontal: 10),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.10),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: color.withValues(alpha: 0.35)),
      ),
      child: Column(
        children: [
          Text(
            value,
            style: TextStyle(
              fontSize: 22,
              fontWeight: FontWeight.w800,
              color: color,
            ),
          ),
          const SizedBox(height: 4),
          Text(
            label,
            textAlign: TextAlign.center,
            style: TextStyle(
              fontSize: 11,
              height: 1.2,
              color: Colors.grey.shade700,
            ),
          ),
        ],
      ),
    );
  }
}

class _SampleBanner extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        color: _accent.withValues(alpha: 0.14),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: _accent.withValues(alpha: 0.5)),
      ),
      child: Row(
        children: [
          const Icon(Icons.science_outlined, size: 18, color: Color(0xFF9A6100)),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              'Sample data — illustrative only, not a real satellite analysis.',
              style: TextStyle(
                fontSize: 12.5,
                fontWeight: FontWeight.w600,
                color: Colors.brown.shade800,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _ErrorBox extends StatelessWidget {
  final String message;
  const _ErrorBox({required this.message});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: const Color(0xFFFDECEA),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: const Color(0xFFF5C6C2)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Icon(Icons.error_outline, size: 18, color: Color(0xFFC62828)),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              message,
              style: const TextStyle(fontSize: 12.5, color: Color(0xFF8A1F1A)),
            ),
          ),
        ],
      ),
    );
  }
}
