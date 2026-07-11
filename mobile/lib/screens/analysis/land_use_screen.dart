import 'package:flutter/material.dart';

import '../../models/analytics_service.dart';
import '../../models/area_bounds.dart';
import '../../models/land_use_result.dart';
import '../../services/land_use_service.dart';
import '../../util/responsive.dart';
import '../../widgets/map_zoom_controls.dart';
import '../../widgets/month_year_field.dart';

const Color _ink = Color(0xFF0E1116);
const Color _accent = Color(0xFFEF9A3D);
const Color _viewerBackground = Color(0xFF0B0D10);

/// Keys the two stacked rasters so tests (and hot reload) can tell them apart.
const Key kLandUseBaseImageKey = Key('landUseBaseImage');
const Key kLandUseOverlayImageKey = Key('landUseOverlayImage');

/// The controls/results panel when it sits beside the scene (wide windows).
const Key kLandUseSidePanelKey = Key('landUseSidePanel');

/// Above this width the panel becomes a right-hand sidebar instead of a bottom
/// sheet — laptops and the web build get the scene and controls side by side.
const double _sidePanelBreakpoint = 820;
const double _sidePanelWidth = 360;

/// Classifies land cover across a selected area for one month and paints the
/// result **on the Sentinel-2 imagery it was computed from** — not on a basemap
/// mosaic. Green for tree cover, blue for water, and so on.
///
/// The backend returns two pixel-aligned rasters for the same bounding box, so
/// they are simply stacked; no reprojection or map is involved. Area selection
/// happens on the previous screen.
///
/// Reached from Analytics → Land Use Classification → Order → select an area.
class LandUseScreen extends StatefulWidget {
  final AreaBounds bounds;
  final AnalyticsService? service;

  const LandUseScreen({super.key, required this.bounds, this.service});

  @override
  State<LandUseScreen> createState() => _LandUseScreenState();
}

class _LandUseScreenState extends State<LandUseScreen> {
  static const double _minScale = 1;
  static const double _maxScale = 10;

  final LandUseService _service = LandUseService();
  final TransformationController _viewer = TransformationController();

  late int _year;
  late int _month;

  bool _loading = false;
  String? _error;
  LandUseResult? _result;

  /// Cached so dragging the opacity slider doesn't re-decode the PNGs.
  MemoryImage? _overlayImage;
  MemoryImage? _baseImage;

  /// Last known size of the viewport, for zooming about its centre.
  Size? _viewportSize;

  double _opacity = 0.65;
  bool _showOverlay = true;

  @override
  void initState() {
    super.initState();
    // Default to the previous month; the current one may have no composite yet.
    final now = DateTime.now();
    final target = DateTime(now.year, now.month - 1);
    _year = target.year;
    _month = target.month;
  }

  @override
  void dispose() {
    _service.dispose();
    _viewer.dispose();
    super.dispose();
  }

  Future<void> _run({bool sample = false}) async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final result = sample
          ? await sampleLandUse(bounds: widget.bounds, year: _year, month: _month)
          : await _service.classify(
              bounds: widget.bounds,
              year: _year,
              month: _month,
            );
      if (!mounted) return;
      setState(() {
        _result = result;
        _overlayImage = MemoryImage(result.imagePng);
        _baseImage =
            result.baseImagePng == null ? null : MemoryImage(result.baseImagePng!);
        _showOverlay = true;
        _loading = false;
      });
      _resetView();
    } on LandUseException catch (e) {
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

  void _reset() {
    _resetView();
    setState(() {
      _result = null;
      _overlayImage = null;
      _baseImage = null;
      _error = null;
    });
  }

  void _resetView() => _viewer.value = Matrix4.identity();

  /// Scales about the centre of the viewport so the middle of the scene stays put.
  void _zoomBy(double factor) {
    final size = _viewportSize;
    if (size == null || size.isEmpty) return;

    final current = _viewer.value.getMaxScaleOnAxis();
    final target = (current * factor).clamp(_minScale, _maxScale);
    if ((target - current).abs() < 0.001) return;

    final centre = Offset(size.width / 2, size.height / 2);
    final anchor = _viewer.toScene(centre);
    _viewer.value = Matrix4.identity()
      ..translateByDouble(centre.dx, centre.dy, 0, 1)
      ..scaleByDouble(target, target, target, 1)
      ..translateByDouble(-anchor.dx, -anchor.dy, 0, 1);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFFF3F4F6),
      appBar: AppBar(
        backgroundColor: Colors.white,
        surfaceTintColor: Colors.white,
        foregroundColor: _ink,
        elevation: 0.5,
        title: Text(
          widget.service?.name ?? 'Land Use Classification',
          style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 18),
        ),
      ),
      body: LayoutBuilder(
        builder: (context, constraints) {
          // Wide (laptop / web): scene on the left, panel as a right sidebar.
          if (constraints.maxWidth > _sidePanelBreakpoint) {
            return Row(
              children: [
                Expanded(child: _buildViewer()),
                _buildSidePanel(context),
              ],
            );
          }
          // Narrow (phone): scene on top, panel as a bottom sheet.
          return Column(
            children: [
              Expanded(child: _buildViewer()),
              _buildPanel(context),
            ],
          );
        },
      ),
    );
  }

  // ── Right-hand sidebar (wide layout) ─────────────────────────────────────
  Widget _buildSidePanel(BuildContext context) {
    return Container(
      key: kLandUseSidePanelKey,
      width: _sidePanelWidth,
      decoration: BoxDecoration(
        color: Colors.white,
        border: const Border(left: BorderSide(color: Colors.black12)),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withValues(alpha: 0.08),
            blurRadius: 16,
            offset: const Offset(-2, 0),
          ),
        ],
      ),
      child: SafeArea(
        left: false,
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(20, 20, 20, 28),
          child: _result != null ? _buildResults(_result!) : _buildControls(),
        ),
      ),
    );
  }

  // ── Scene viewer ─────────────────────────────────────────────────────────
  Widget _buildViewer() {
    final result = _result;

    return Stack(
      children: [
        Positioned.fill(
          child: ColoredBox(
            color: _viewerBackground,
            child: result == null ? _buildPlaceholder() : _buildScene(result),
          ),
        ),
        if (result != null && _showOverlay) _buildLegend(result),
        if (result != null)
          Positioned(
            right: 16,
            bottom: 16,
            child: MapZoomControls(
              onZoomIn: () => _zoomBy(1.5),
              onZoomOut: () => _zoomBy(1 / 1.5),
              onReset: _resetView,
            ),
          ),
        if (_loading) _buildLoadingOverlay(),
      ],
    );
  }

  /// The Sentinel-2 composite with the class mask stacked on top.
  ///
  /// Both rasters share a grid and a size, so an [AspectRatio] box plus
  /// [BoxFit.fill] lines them up exactly — no resampling, no reprojection.
  Widget _buildScene(LandUseResult result) {
    final base = _baseImage;
    final overlay = _overlayImage;

    return LayoutBuilder(
      builder: (context, constraints) {
        _viewportSize = constraints.biggest;
        return InteractiveViewer(
          transformationController: _viewer,
          minScale: _minScale,
          maxScale: _maxScale,
          child: Center(
            child: AspectRatio(
              aspectRatio: result.aspectRatio,
              child: Stack(
                fit: StackFit.expand,
                children: [
                  if (base != null)
                    Image(
                      key: kLandUseBaseImageKey,
                      image: base,
                      fit: BoxFit.fill,
                      filterQuality: FilterQuality.medium,
                      gaplessPlayback: true,
                    ),
                  if (overlay != null && _showOverlay)
                    Opacity(
                      opacity: _opacity,
                      child: Image(
                        key: kLandUseOverlayImageKey,
                        image: overlay,
                        fit: BoxFit.fill,
                        // Keep class edges crisp rather than smeared.
                        filterQuality: FilterQuality.none,
                        gaplessPlayback: true,
                      ),
                    ),
                ],
              ),
            ),
          ),
        );
      },
    );
  }

  Widget _buildPlaceholder() {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.satellite_alt, size: 56, color: Colors.white24),
            const SizedBox(height: 16),
            const Text(
              'The Sentinel-2 scene appears here',
              style: TextStyle(
                color: Colors.white70,
                fontSize: 15,
                fontWeight: FontWeight.w600,
              ),
            ),
            const SizedBox(height: 6),
            Text(
              '${widget.bounds.areaKm2.toStringAsFixed(2)} km² selected — run the '
              'classification to fetch the imagery and label it.',
              textAlign: TextAlign.center,
              style: const TextStyle(color: Colors.white38, fontSize: 13),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildLegend(LandUseResult result) {
    final present = result.presentClasses;
    if (present.isEmpty) return const SizedBox.shrink();

    return Positioned(
      top: 12,
      left: 12,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        decoration: BoxDecoration(
          color: Colors.black.withValues(alpha: 0.72),
          borderRadius: BorderRadius.circular(10),
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            for (final c in present) ...[
              _LegendRow(color: c.color, label: c.name, percent: c.percent),
              if (c != present.last) const SizedBox(height: 6),
            ],
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
                'Classifying land cover…',
                style: TextStyle(
                  color: Colors.white,
                  fontSize: 16,
                  fontWeight: FontWeight.w600,
                ),
              ),
              SizedBox(height: 6),
              Text(
                'Building the Sentinel-2 composite and labelling every\npixel — this can take a few minutes.',
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
    return Material(
      color: Colors.white,
      elevation: 12,
      child: SafeArea(
        top: false,
        child: ConstrainedBox(
          constraints: BoxConstraints(maxHeight: context.screenHeight * 0.5),
          child: SingleChildScrollView(
            child: ResponsiveCenter(
              maxWidth: 720,
              padding: const EdgeInsets.fromLTRB(20, 16, 20, 16),
              child: _result != null ? _buildResults(_result!) : _buildControls(),
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
          'Pull the Sentinel-2 scene for this area, label every pixel as tree '
          'cover, crop, water, or bare soil, and draw the result on it.',
          style: TextStyle(fontSize: 13, color: Colors.grey.shade600),
        ),
        const SizedBox(height: 16),
        MonthYearField(
          label: 'Composite date',
          year: _year,
          month: _month,
          onYear: (y) => setState(() => _year = y),
          onMonth: (m) => setState(() => _month = m),
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
            icon: const Icon(Icons.layers_outlined, size: 20),
            label: const Text(
              'RUN CLASSIFICATION',
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

  Widget _buildResults(LandUseResult result) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (result.isSample) ...[
          const _SampleBanner(),
          const SizedBox(height: 12),
        ],
        Row(
          children: [
            const Icon(Icons.layers_outlined, size: 20, color: _ink),
            const SizedBox(width: 8),
            const Expanded(
              child: Text(
                'Land cover',
                style: TextStyle(
                  fontSize: 18,
                  fontWeight: FontWeight.w800,
                  color: _ink,
                ),
              ),
            ),
            Text(
              '${result.date}  ·  ${result.resolutionMeters} m/px',
              style: TextStyle(fontSize: 12, color: Colors.grey.shade600),
            ),
          ],
        ),
        const SizedBox(height: 12),
        _buildOverlayControls(),
        const SizedBox(height: 8),
        for (final c in result.presentClasses) _ClassRow(landCover: c),
        if (result.coveragePercent < 99.5) ...[
          const SizedBox(height: 10),
          Text(
            '${(100 - result.coveragePercent).toStringAsFixed(1)}% of the area had no '
            'cloud-free observation and is left unpainted.',
            style: TextStyle(fontSize: 12, color: Colors.grey.shade600),
          ),
        ],
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
              'CHANGE DATE & RUN AGAIN',
              style: TextStyle(fontWeight: FontWeight.w700, letterSpacing: 0.3),
            ),
          ),
        ),
      ],
    );
  }

  Widget _buildOverlayControls() {
    return Row(
      children: [
        IconButton(
          onPressed: () => setState(() => _showOverlay = !_showOverlay),
          tooltip: _showOverlay
              ? 'Hide the mask to see the raw Sentinel-2 scene'
              : 'Show the mask',
          icon: Icon(
            _showOverlay ? Icons.visibility_outlined : Icons.visibility_off_outlined,
            size: 20,
            color: _showOverlay ? _ink : Colors.grey.shade500,
          ),
        ),
        Expanded(
          child: Slider(
            value: _opacity,
            min: 0.15,
            max: 1,
            activeColor: _accent,
            label: '${(_opacity * 100).round()}%',
            divisions: 17,
            onChanged: _showOverlay
                ? (v) => setState(() => _opacity = v)
                : null,
          ),
        ),
        SizedBox(
          width: 44,
          child: Text(
            '${(_opacity * 100).round()}%',
            textAlign: TextAlign.end,
            style: TextStyle(fontSize: 12, color: Colors.grey.shade700),
          ),
        ),
      ],
    );
  }
}

// ── Small presentational widgets ────────────────────────────────────────────

class _LegendRow extends StatelessWidget {
  final Color color;
  final String label;
  final double percent;

  const _LegendRow({
    required this.color,
    required this.label,
    required this.percent,
  });

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: 12,
          height: 12,
          decoration: BoxDecoration(
            color: color,
            borderRadius: BorderRadius.circular(3),
          ),
        ),
        const SizedBox(width: 8),
        Text(label, style: const TextStyle(color: Colors.white, fontSize: 12)),
        const SizedBox(width: 10),
        Text(
          '${percent.toStringAsFixed(1)}%',
          style: const TextStyle(color: Colors.white70, fontSize: 12),
        ),
      ],
    );
  }
}

class _ClassRow extends StatelessWidget {
  final LandCoverClass landCover;

  const _ClassRow({required this.landCover});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        children: [
          Container(
            width: 14,
            height: 14,
            decoration: BoxDecoration(
              color: landCover.color,
              borderRadius: BorderRadius.circular(4),
            ),
          ),
          const SizedBox(width: 10),
          SizedBox(
            width: 74,
            child: Text(
              landCover.name,
              style: const TextStyle(
                fontSize: 13,
                fontWeight: FontWeight.w600,
                color: _ink,
              ),
            ),
          ),
          Expanded(
            child: ClipRRect(
              borderRadius: BorderRadius.circular(4),
              child: LinearProgressIndicator(
                value: (landCover.percent / 100).clamp(0.0, 1.0),
                minHeight: 8,
                backgroundColor: Colors.grey.shade200,
                valueColor: AlwaysStoppedAnimation<Color>(landCover.color),
              ),
            ),
          ),
          SizedBox(
            width: 56,
            child: Text(
              '${landCover.percent.toStringAsFixed(1)}%',
              textAlign: TextAlign.end,
              style: const TextStyle(
                fontSize: 13,
                fontWeight: FontWeight.w700,
                color: _ink,
              ),
            ),
          ),
          SizedBox(
            width: 78,
            child: Text(
              '${landCover.areaKm2.toStringAsFixed(2)} km²',
              textAlign: TextAlign.end,
              style: TextStyle(fontSize: 12, color: Colors.grey.shade600),
            ),
          ),
        ],
      ),
    );
  }
}

class _SampleBanner extends StatelessWidget {
  const _SampleBanner();

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
