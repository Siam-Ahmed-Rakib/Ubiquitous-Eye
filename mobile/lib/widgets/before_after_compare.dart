import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../models/analysis_result.dart';
import '../models/land_use_result.dart';

const Color _ink = Color(0xFF0E1116);
const Color _muted = Color(0xFF6B7280);
const Color _line = Color(0xFFE3E6EA);
const Color _frame = Color(0xFF10141A);

/// Height ceilings for the pictures, in logical pixels.
///
/// Without them the aspect ratio alone decides the height, and a tall narrow
/// area of interest — a river corridor, a coastline — renders metres of image
/// that has to be scrolled past. The difference is allowed to be the larger of
/// the two because it is the headline result, but only by a little: it sits
/// under both panes, so anything more and the comparison scrolls off screen.
const double _paneMaxHeight = 460;
const double _differenceMaxHeight = 560;

/// Before / after / difference view of one change analysis.
///
/// Three pictures, all spanning the same ground at the same size, so they
/// register cell for cell:
///
///  * the two dated scenes, side by side, as the eye reads them — before on the
///    left, after on the right;
///  * beneath them, the difference: what actually moved between the two.
///
/// One switch drives all three. Off, they show the raw Sentinel-2 imagery. On,
/// they show each date's land cover painted by class — green tree, blue water,
/// tan soil — shaded by the scene's own brightness and outlined where two
/// classes meet. The point of putting them under one control is that the same
/// ground is being described two ways: flick it and a river that was a dark
/// smudge becomes unmistakably blue, on both dates at once.
///
/// Every pane shares a single [TransformationController], so zooming or panning
/// any one moves all of them. At any zoom the three show the same ground, which
/// is what makes them comparable by eye rather than by hunting for the matching
/// spot.
class BeforeAfterCompare extends StatefulWidget {
  final AnalysisResult result;

  const BeforeAfterCompare({super.key, required this.result});

  @override
  State<BeforeAfterCompare> createState() => _BeforeAfterCompareState();
}

class _BeforeAfterCompareState extends State<BeforeAfterCompare> {
  final TransformationController _transform = TransformationController();

  /// Land-cover colouring on. Starts on when the backend sent class maps: the
  /// classification is the thing this screen has to say, and the raw scene is
  /// one switch away for anyone who wants to check it against the ground.
  late bool _classified = widget.result.hasClassMaps;

  bool _showDeforestation = true;
  bool _showWaterLoss = true;

  @override
  void dispose() {
    _transform.dispose();
    super.dispose();
  }

  void _resetZoom() => _transform.value = Matrix4.identity();

  /// Whether the class map is actually being drawn, rather than merely asked
  /// for — a result without class maps stays on the raw imagery.
  bool get _showingClasses => _classified && widget.result.hasClassMaps;

  @override
  Widget build(BuildContext context) {
    final result = widget.result;
    final stats = result.stats;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      mainAxisSize: MainAxisSize.min,
      children: [
        _buildToggleBar(),
        const SizedBox(height: 14),
        // Side by side, at every width. The comparison is the product: stacking
        // them would put the two dates a scroll apart and there would be
        // nothing left to compare at a glance.
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: _ScenePane(
                label: 'BEFORE',
                // The window the composite covers, not just the month, so the
                // imagery can be matched against the date it claims.
                date: stats?.oldWindow.isNotEmpty == true
                    ? stats!.oldWindow
                    : (stats?.oldDate ?? ''),
                image: result.oldImagePng,
                classImage: result.oldClassPng,
                classes: result.oldClasses,
                showClasses: _showingClasses,
                aspectRatio: result.aspectRatio,
                transform: _transform,
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: _ScenePane(
                label: 'AFTER',
                date: stats?.newWindow.isNotEmpty == true
                    ? stats!.newWindow
                    : (stats?.newDate ?? ''),
                image: result.newImagePng,
                classImage: result.newClassPng,
                classes: result.newClasses,
                showClasses: _showingClasses,
                aspectRatio: result.aspectRatio,
                transform: _transform,
              ),
            ),
          ],
        ),
        const SizedBox(height: 22),
        _buildDifference(result),
      ],
    );
  }

  // ── The switch ───────────────────────────────────────────────────────────
  /// The one control for all three pictures, with the legend for what it turns
  /// on sitting beside it — so the colours are explained in the same glance as
  /// the switch that produces them.
  Widget _buildToggleBar() {
    final available = widget.result.hasClassMaps;
    return Container(
      padding: const EdgeInsets.fromLTRB(14, 10, 10, 10),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: _line),
      ),
      // Wraps rather than overflows: switch, label and three legend swatches do
      // not fit one line on a compact phone.
      child: Wrap(
        crossAxisAlignment: WrapCrossAlignment.center,
        alignment: WrapAlignment.spaceBetween,
        spacing: 16,
        runSpacing: 10,
        children: [
          Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(
                _showingClasses ? Icons.palette : Icons.palette_outlined,
                size: 19,
                color: _showingClasses ? _ink : _muted,
              ),
              const SizedBox(width: 9),
              Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  const Text(
                    'Land cover',
                    style: TextStyle(
                      fontSize: 13.5,
                      fontWeight: FontWeight.w700,
                      color: _ink,
                    ),
                  ),
                  Text(
                    available
                        ? (_showingClasses ? 'Coloured by class' : 'Raw imagery')
                        : 'Unavailable for this run',
                    style: const TextStyle(fontSize: 11.5, color: _muted),
                  ),
                ],
              ),
              const SizedBox(width: 6),
              Switch(
                value: _showingClasses,
                activeThumbColor: Colors.white,
                activeTrackColor: const Color(0xFF2D7D32),
                onChanged:
                    available ? (v) => setState(() => _classified = v) : null,
              ),
            ],
          ),
          // Only meaningful while the colouring is on.
          AnimatedOpacity(
            duration: const Duration(milliseconds: 180),
            opacity: _showingClasses ? 1 : 0.25,
            child: Wrap(
              spacing: 14,
              runSpacing: 6,
              children: [
                for (final entry in kClassMapPalette.entries)
                  _LegendSwatch(color: entry.value, label: entry.key),
              ],
            ),
          ),
        ],
      ),
    );
  }

  // ── The difference ───────────────────────────────────────────────────────
  /// The third picture: the change itself, full width beneath the pair that
  /// produced it.
  ///
  /// The base is dimmed so the change colours carry all the contrast — the
  /// question this panel answers is *where*, and a full-strength backdrop
  /// competes with the answer.
  Widget _buildDifference(AnalysisResult result) {
    final deforestation = result.deforestationCount;
    final waterLoss = result.waterLossCount;
    final base = _showingClasses ? result.newClassPng : result.newImagePng;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      mainAxisSize: MainAxisSize.min,
      children: [
        Row(
          children: [
            const Text(
              'CHANGE',
              style: TextStyle(
                fontSize: 11,
                fontWeight: FontWeight.w800,
                letterSpacing: 1.1,
                color: _ink,
              ),
            ),
            const SizedBox(width: 8),
            const Expanded(
              child: Text(
                'what moved between the two dates',
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(fontSize: 11.5, color: _muted),
              ),
            ),
            IconButton(
              onPressed: _resetZoom,
              tooltip: 'Reset zoom',
              visualDensity: VisualDensity.compact,
              icon: const Icon(Icons.zoom_out_map, size: 19),
              color: _muted,
            ),
          ],
        ),
        const SizedBox(height: 6),
        _BoundedPicture(
          aspectRatio: result.aspectRatio,
          maxHeight: _differenceMaxHeight,
          transform: _transform,
          // Keyed off the counts the chips below display, not off the
          // `changes` list: the two disagree whenever the backend sends stats
          // without the per-pixel list, and a panel captioned "1205" over the
          // words "no change" is worse than either message alone.
          overlay: (deforestation == 0 && waterLoss == 0)
              ? const _NoChangeOverlay()
              : null,
          children: [
            if (base != null)
              Image.memory(
                base,
                fit: BoxFit.fill,
                filterQuality: FilterQuality.medium,
                gaplessPlayback: true,
              ),
            // The scrim is what lets a few hundred red pixels read against a
            // whole scene of ground.
            const ColoredBox(color: Color(0x8C0B0E12)),
            // Water loss first, so deforestation wins where they touch.
            if (_showWaterLoss && result.waterLossPng != null)
              Image.memory(
                result.waterLossPng!,
                fit: BoxFit.fill,
                filterQuality: FilterQuality.none,
                gaplessPlayback: true,
              ),
            if (_showDeforestation && result.deforestationPng != null)
              Image.memory(
                result.deforestationPng!,
                fit: BoxFit.fill,
                filterQuality: FilterQuality.none,
                gaplessPlayback: true,
              ),
          ],
        ),
        const SizedBox(height: 10),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            _ChangeChip(
              color: kDeforestationColor,
              label: 'Deforestation',
              count: deforestation,
              selected: _showDeforestation,
              onChanged: (v) => setState(() => _showDeforestation = v),
            ),
            _ChangeChip(
              color: kWaterLossColor,
              label: 'Water loss',
              count: waterLoss,
              selected: _showWaterLoss,
              onChanged: (v) => setState(() => _showWaterLoss = v),
            ),
          ],
        ),
        const SizedBox(height: 10),
        const Text(
          'Tap a colour to show or hide it. Pinch or scroll any picture to '
          'zoom — all three move together. Changed cells are drawn slightly '
          'larger than their true 10 m footprint so they stay visible, but the '
          'counts are exact.',
          style: TextStyle(fontSize: 11.5, height: 1.35, color: _muted),
        ),
      ],
    );
  }
}

// ── One dated scene ─────────────────────────────────────────────────────────

/// A labelled, dated picture of one date, plus what it is made of.
class _ScenePane extends StatelessWidget {
  final String label;
  final String date;
  final Uint8List? image;
  final Uint8List? classImage;
  final List<LandCoverClass> classes;
  final bool showClasses;
  final double aspectRatio;
  final TransformationController transform;

  const _ScenePane({
    required this.label,
    required this.date,
    required this.image,
    required this.classImage,
    required this.classes,
    required this.showClasses,
    required this.aspectRatio,
    required this.transform,
  });

  @override
  Widget build(BuildContext context) {
    final shown = showClasses ? classImage : image;

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
            const SizedBox(width: 6),
            Expanded(
              child: Text(
                date,
                textAlign: TextAlign.end,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(fontSize: 11, color: _muted),
              ),
            ),
          ],
        ),
        const SizedBox(height: 6),
        _BoundedPicture(
          aspectRatio: aspectRatio,
          maxHeight: _paneMaxHeight,
          transform: transform,
          children: [
            // Cross-fade rather than cut, so it reads as one piece of ground
            // being re-described rather than two unrelated pictures.
            AnimatedSwitcher(
              duration: const Duration(milliseconds: 220),
              child: shown == null
                  ? const SizedBox.expand(key: ValueKey('none'))
                  : Image.memory(
                      shown,
                      key: ValueKey(showClasses),
                      fit: BoxFit.fill,
                      filterQuality: FilterQuality.medium,
                      gaplessPlayback: true,
                    ),
            ),
          ],
        ),
        // Describes the class map, so it goes when the class map goes.
        if (showClasses && classes.isNotEmpty) ...[
          const SizedBox(height: 8),
          _ClassShareBar(classes: classes),
        ],
      ],
    );
  }
}

/// A rounded, zoomable frame holding one stack of pixel-aligned rasters.
///
/// Every picture on this screen is built from it, which is what makes the three
/// interchangeable: same corner radius, same backdrop behind a transparent
/// raster, same zoom behaviour, and — because they share one controller — the
/// same viewport.
///
/// [Center] is load-bearing rather than cosmetic. Inside a Row's [Expanded] the
/// width arrives tight, and an [AspectRatio] under a tight width cannot shrink
/// to honour [maxHeight] — it stretches instead, distorting the picture and,
/// worse, sliding the change mask off the ground it describes. Centring first
/// loosens the constraint, so the aspect wins and the height cap is respected.
class _BoundedPicture extends StatelessWidget {
  final double aspectRatio;
  final double maxHeight;
  final TransformationController transform;

  /// Stacked bottom-first, each filling the frame exactly. These are the
  /// rasters, so they live *inside* the zoom and move with the ground.
  final List<Widget> children;

  /// Drawn over the frame but outside the zoom, so it holds its size and
  /// position however far the picture underneath is scaled.
  final Widget? overlay;

  const _BoundedPicture({
    required this.aspectRatio,
    required this.maxHeight,
    required this.transform,
    required this.children,
    this.overlay,
  });

  @override
  Widget build(BuildContext context) {
    return Center(
      child: ConstrainedBox(
        constraints: BoxConstraints(maxHeight: maxHeight),
        child: AspectRatio(
          aspectRatio: aspectRatio,
          child: ClipRRect(
            borderRadius: BorderRadius.circular(12),
            child: ColoredBox(
              color: _frame,
              child: Stack(
                fit: StackFit.expand,
                children: [
                  InteractiveViewer(
                    transformationController: transform,
                    minScale: 1,
                    maxScale: 8,
                    child: Stack(fit: StackFit.expand, children: children),
                  ),
                  if (overlay != null) IgnorePointer(child: overlay),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

// ── Small presentational widgets ────────────────────────────────────────────

/// A stacked proportion bar over the classes present, with their shares.
///
/// Placed under each date so the two bars sit one above the other in the
/// layout: the composition shift between the dates is then readable without
/// interpreting the pictures at all.
class _ClassShareBar extends StatelessWidget {
  final List<LandCoverClass> classes;

  const _ClassShareBar({required this.classes});

  @override
  Widget build(BuildContext context) {
    final present = classes.where((c) => c.pixels > 0).toList();
    if (present.isEmpty) return const SizedBox.shrink();

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      mainAxisSize: MainAxisSize.min,
      children: [
        ClipRRect(
          borderRadius: BorderRadius.circular(3),
          child: SizedBox(
            height: 6,
            child: Row(
              // Load-bearing: a Row centres by default, which hands its
              // children a *loose* height, and a childless ColoredBox under a
              // loose height takes the smallest it is allowed — zero. Stretch
              // makes the segments fill the 6px the SizedBox reserved.
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                for (final c in present)
                  Expanded(
                    flex: c.pixels,
                    child: ColoredBox(
                      color: kClassMapPalette[c.name] ?? c.color,
                    ),
                  ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 6),
        Wrap(
          spacing: 10,
          runSpacing: 3,
          children: [
            for (final c in present)
              Text(
                '${c.name} ${c.percent.toStringAsFixed(0)}%',
                style: TextStyle(
                  fontSize: 10.5,
                  fontWeight: FontWeight.w600,
                  color: kClassMapPalette[c.name] ?? c.color,
                ),
              ),
          ],
        ),
      ],
    );
  }
}

/// A colour chip and its class name, for the land-cover legend.
class _LegendSwatch extends StatelessWidget {
  final Color color;
  final String label;

  const _LegendSwatch({required this.color, required this.label});

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: 13,
          height: 13,
          decoration: BoxDecoration(
            color: color,
            borderRadius: BorderRadius.circular(3.5),
            // The same hairline the raster draws between classes, so the key
            // looks like the thing it is a key to.
            border: Border.all(color: const Color(0xFF090B0D), width: 1),
          ),
        ),
        const SizedBox(width: 6),
        Text(
          label,
          style: const TextStyle(
            fontSize: 12,
            fontWeight: FontWeight.w600,
            color: _ink,
          ),
        ),
      ],
    );
  }
}

/// A colour-keyed chip that shows a change class's count and toggles its raster.
class _ChangeChip extends StatelessWidget {
  final Color color;
  final String label;
  final int count;
  final bool selected;
  final ValueChanged<bool> onChanged;

  const _ChangeChip({
    required this.color,
    required this.label,
    required this.count,
    required this.selected,
    required this.onChanged,
  });

  @override
  Widget build(BuildContext context) {
    final enabled = count > 0;
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
              color: active ? color.withValues(alpha: 0.7) : _line,
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
                  color: active ? _ink : _muted,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// Shown over the difference picture when the two dates came back identical —
/// an empty black frame reads as a failure, and this one is a real result.
class _NoChangeOverlay extends StatelessWidget {
  const _NoChangeOverlay();

  @override
  Widget build(BuildContext context) {
    return const Center(
      child: Padding(
        padding: EdgeInsets.all(16),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.check_circle_outline, size: 26, color: Colors.white70),
            SizedBox(height: 8),
            Text(
              'No change detected',
              textAlign: TextAlign.center,
              style: TextStyle(
                color: Colors.white,
                fontSize: 13,
                fontWeight: FontWeight.w600,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
