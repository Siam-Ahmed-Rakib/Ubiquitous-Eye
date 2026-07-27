import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../models/analysis_result.dart';
import '../util/responsive.dart';

const Color _ink = Color(0xFF0E1116);

/// Side-by-side before/after view of the two Sentinel-2 scenes a change
/// analysis compared, with the change mask painted over both.
///
/// The three rasters the backend returns span the same bounding box and are the
/// same size, so stacking them in one aspect box registers them cell for cell —
/// a red pixel sits exactly on the ground it was derived from.
///
/// Both panes share a single [TransformationController], so zooming or panning
/// one moves the other identically. That is the whole point of the view: at any
/// zoom the two panes show the same ground, so the eye can compare them
/// directly instead of hunting for the matching spot.
class BeforeAfterCompare extends StatefulWidget {
  final AnalysisResult result;

  const BeforeAfterCompare({super.key, required this.result});

  @override
  State<BeforeAfterCompare> createState() => _BeforeAfterCompareState();
}

class _BeforeAfterCompareState extends State<BeforeAfterCompare> {
  final TransformationController _transform = TransformationController();

  double _opacity = 0.85;
  bool _showDeforestation = true;
  bool _showWaterLoss = true;

  @override
  void dispose() {
    _transform.dispose();
    super.dispose();
  }

  void _resetZoom() => _transform.value = Matrix4.identity();

  @override
  Widget build(BuildContext context) {
    final result = widget.result;
    final stats = result.stats;
    final panes = [
      _ScenePane(
        label: 'BEFORE',
        // The window the composite covers, not just the month, so the imagery
        // can be matched against the date it claims.
        date: stats?.oldWindow.isNotEmpty == true
            ? stats!.oldWindow
            : (stats?.oldDate ?? ''),
        image: result.oldImagePng,
        result: result,
        transform: _transform,
        opacity: _opacity,
        showDeforestation: _showDeforestation,
        showWaterLoss: _showWaterLoss,
        // Left unmarked on purpose: this pane is the reference. Painting over it
        // would hide the ground you are trying to compare the marks against.
        marked: false,
      ),
      _ScenePane(
        label: 'AFTER',
        date: stats?.newWindow.isNotEmpty == true
            ? stats!.newWindow
            : (stats?.newDate ?? ''),
        image: result.newImagePng,
        result: result,
        transform: _transform,
        opacity: _opacity,
        showDeforestation: _showDeforestation,
        showWaterLoss: _showWaterLoss,
        marked: true,
      ),
    ];

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      mainAxisSize: MainAxisSize.min,
      children: [
        // Wide windows put the two scenes shoulder to shoulder; a phone stacks
        // them, which still reads top-to-bottom as before -> after.
        if (context.isWide)
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(child: panes[0]),
              const SizedBox(width: 12),
              Expanded(child: panes[1]),
            ],
          )
        else
          Column(
            children: [
              panes[0],
              const SizedBox(height: 12),
              panes[1],
            ],
          ),
        const SizedBox(height: 14),
        _buildControls(result),
      ],
    );
  }

  Widget _buildControls(AnalysisResult result) {
    final hasDeforestation = result.deforestationCount > 0;
    final hasWaterLoss = result.waterLossCount > 0;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(
          'The coloured pixels are the detected changes, marked on the AFTER '
          'scene only. BEFORE is left clear, so you can look straight across to '
          'see what each marked spot used to be.',
          style: TextStyle(fontSize: 12.5, height: 1.4, color: Colors.grey.shade700),
        ),
        const SizedBox(height: 4),
        Row(
          children: [
            const Icon(Icons.layers_outlined, size: 18, color: _ink),
            const SizedBox(width: 6),
            Text(
              'Mark visibility',
              style: TextStyle(
                fontSize: 12.5,
                fontWeight: FontWeight.w700,
                color: Colors.grey.shade800,
              ),
            ),
            Expanded(
              child: Slider(
                value: _opacity,
                min: 0,
                max: 1,
                activeColor: kDeforestationColor,
                // Drag to 0 to clear the marks and read the bare scenes.
                onChanged: (v) => setState(() => _opacity = v),
              ),
            ),
            SizedBox(
              width: 62,
              child: Text(
                _opacity == 0 ? 'hidden' : '${(_opacity * 100).round()}%',
                textAlign: TextAlign.right,
                style: TextStyle(fontSize: 12, color: Colors.grey.shade700),
              ),
            ),
            IconButton(
              onPressed: _resetZoom,
              tooltip: 'Reset zoom',
              icon: const Icon(Icons.zoom_out_map, size: 20),
              color: Colors.grey.shade700,
            ),
          ],
        ),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            _ClassToggle(
              color: kDeforestationColor,
              label: 'Deforestation',
              count: result.deforestationCount,
              enabled: hasDeforestation,
              selected: _showDeforestation && hasDeforestation,
              onChanged: (v) => setState(() => _showDeforestation = v),
            ),
            _ClassToggle(
              color: kWaterLossColor,
              label: 'Water loss',
              count: result.waterLossCount,
              enabled: hasWaterLoss,
              selected: _showWaterLoss && hasWaterLoss,
              onChanged: (v) => setState(() => _showWaterLoss = v),
            ),
          ],
        ),
        const SizedBox(height: 10),
        Text(
          'Tap a colour to show or hide it — turn off water loss to study '
          'deforestation on its own. Pinch or scroll either scene to zoom; both '
          'move together. Marks are drawn slightly larger than their true 10 m '
          'footprint so they stay visible, but the counts are exact.',
          style: TextStyle(fontSize: 11.5, height: 1.35, color: Colors.grey.shade600),
        ),
      ],
    );
  }
}

/// One dated scene with the change mask stacked over it.
class _ScenePane extends StatelessWidget {
  final String label;
  final String date;
  final Uint8List? image;
  final AnalysisResult result;
  final TransformationController transform;
  final double opacity;
  final bool showDeforestation;
  final bool showWaterLoss;

  /// Whether the change mask is painted on this scene. Only the newer one is,
  /// so the older stays a clean reference.
  final bool marked;

  const _ScenePane({
    required this.label,
    required this.date,
    required this.image,
    required this.result,
    required this.transform,
    required this.opacity,
    required this.showDeforestation,
    required this.showWaterLoss,
    required this.marked,
  });

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      mainAxisSize: MainAxisSize.min,
      children: [
        Row(
          children: [
            Text(
              label,
              style: const TextStyle(
                fontSize: 11,
                fontWeight: FontWeight.w800,
                letterSpacing: 1.1,
                color: _ink,
              ),
            ),
            const Spacer(),
            Text(
              date,
              style: TextStyle(fontSize: 11.5, color: Colors.grey.shade600),
            ),
          ],
        ),
        const SizedBox(height: 6),
        ClipRRect(
          borderRadius: BorderRadius.circular(10),
          child: AspectRatio(
            aspectRatio: result.aspectRatio,
            child: ColoredBox(
              color: const Color(0xFF10141A),
              child: InteractiveViewer(
                transformationController: transform,
                minScale: 1,
                maxScale: 8,
                child: Stack(
                  fit: StackFit.expand,
                  children: [
                    if (image != null)
                      Image.memory(
                        image!,
                        fit: BoxFit.fill,
                        filterQuality: FilterQuality.medium,
                        gaplessPlayback: true,
                      ),
                    // Both masks fill the same box as the scene, so they line up
                    // without any per-pixel maths on this side.
                    if (marked && showWaterLoss && result.waterLossPng != null)
                      Opacity(
                        opacity: opacity,
                        child: Image.memory(
                          result.waterLossPng!,
                          fit: BoxFit.fill,
                          filterQuality: FilterQuality.none,
                          gaplessPlayback: true,
                        ),
                      ),
                    // Deforestation last so red wins where the two touch.
                    if (marked && showDeforestation && result.deforestationPng != null)
                      Opacity(
                        opacity: opacity,
                        child: Image.memory(
                          result.deforestationPng!,
                          fit: BoxFit.fill,
                          filterQuality: FilterQuality.none,
                          gaplessPlayback: true,
                        ),
                      ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ],
    );
  }
}

/// A colour-keyed chip that shows a class's count and toggles its raster.
class _ClassToggle extends StatelessWidget {
  final Color color;
  final String label;
  final int count;
  final bool enabled;
  final bool selected;
  final ValueChanged<bool> onChanged;

  const _ClassToggle({
    required this.color,
    required this.label,
    required this.count,
    required this.enabled,
    required this.selected,
    required this.onChanged,
  });

  @override
  Widget build(BuildContext context) {
    final active = selected && enabled;
    return Opacity(
      opacity: enabled ? 1 : 0.45,
      child: InkWell(
        borderRadius: BorderRadius.circular(20),
        onTap: enabled ? () => onChanged(!selected) : null,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
          decoration: BoxDecoration(
            color: active ? color.withValues(alpha: 0.12) : Colors.transparent,
            borderRadius: BorderRadius.circular(20),
            border: Border.all(
              color: active ? color.withValues(alpha: 0.7) : Colors.grey.shade400,
              width: active ? 1.4 : 1,
            ),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Container(
                width: 11,
                height: 11,
                decoration: BoxDecoration(
                  color: active ? color : Colors.grey.shade400,
                  shape: BoxShape.circle,
                ),
              ),
              const SizedBox(width: 8),
              Text(
                enabled ? '$label · $count' : '$label · none',
                style: TextStyle(
                  fontSize: 12,
                  fontWeight: FontWeight.w600,
                  color: active ? _ink : Colors.grey.shade600,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
