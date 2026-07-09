import 'package:flutter/material.dart';

const Color _accent = Color(0xFFEF9A3D);

/// The small two-button cluster on the right of the search bar:
/// a layers toggle (satellite ↔ street) and a reset-selection-box button.
class MapControls extends StatelessWidget {
  final bool satellite;
  final VoidCallback onToggleLayers;
  final VoidCallback onResetSelection;

  const MapControls({
    super.key,
    required this.satellite,
    required this.onToggleLayers,
    required this.onResetSelection,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 52,
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
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          _IconButton(
            icon: Icons.layers_outlined,
            tooltip: satellite ? 'Switch to street map' : 'Switch to satellite',
            active: satellite,
            onTap: onToggleLayers,
          ),
          Container(width: 1, height: 26, color: Colors.black12),
          _IconButton(
            icon: Icons.crop_free,
            tooltip: 'Reset selection box to map centre',
            active: false,
            onTap: onResetSelection,
          ),
        ],
      ),
    );
  }
}

class _IconButton extends StatelessWidget {
  final IconData icon;
  final String tooltip;
  final bool active;
  final VoidCallback onTap;

  const _IconButton({
    required this.icon,
    required this.tooltip,
    required this.active,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return Tooltip(
      message: tooltip,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(12),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12),
          child: Icon(
            icon,
            size: 24,
            color: active ? _accent : Colors.black87,
          ),
        ),
      ),
    );
  }
}
