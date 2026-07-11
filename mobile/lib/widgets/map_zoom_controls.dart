import 'package:flutter/material.dart';

const Color _accent = Color(0xFFEF9A3D);

/// Vertical zoom cluster for the bottom-right of a map or image viewer.
///
/// Explicit buttons matter on laptops: a touchpad's scroll-to-zoom is fiddly,
/// and there is no pinch gesture without a touchscreen.
///
/// [onReset] adds a fit-to-view button. When [boxLocked] is non-null a lock
/// toggle is added: locking the selection box lets the map be panned by
/// dragging anywhere, including across the box.
class MapZoomControls extends StatelessWidget {
  final VoidCallback onZoomIn;
  final VoidCallback onZoomOut;
  final VoidCallback? onReset;
  final bool? boxLocked;
  final VoidCallback? onToggleBoxLock;

  const MapZoomControls({
    super.key,
    required this.onZoomIn,
    required this.onZoomOut,
    this.onReset,
    this.boxLocked,
    this.onToggleBoxLock,
  });

  @override
  Widget build(BuildContext context) {
    final locked = boxLocked;

    return Container(
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withValues(alpha: 0.25),
            blurRadius: 8,
            offset: const Offset(0, 2),
          ),
        ],
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          _Button(
            icon: Icons.add,
            tooltip: 'Zoom in',
            onTap: onZoomIn,
          ),
          const _Divider(),
          _Button(
            icon: Icons.remove,
            tooltip: 'Zoom out',
            onTap: onZoomOut,
          ),
          if (onReset != null) ...[
            const _Divider(),
            _Button(
              icon: Icons.center_focus_strong_outlined,
              tooltip: 'Fit to view',
              onTap: onReset!,
            ),
          ],
          if (locked != null && onToggleBoxLock != null) ...[
            const _Divider(),
            _Button(
              icon: locked ? Icons.lock_outline : Icons.lock_open_outlined,
              tooltip: locked
                  ? 'Unlock the selection box'
                  : 'Lock the selection box so you can pan the map through it',
              active: locked,
              onTap: onToggleBoxLock!,
            ),
          ],
        ],
      ),
    );
  }
}

class _Divider extends StatelessWidget {
  const _Divider();

  @override
  Widget build(BuildContext context) =>
      Container(width: 26, height: 1, color: Colors.black12);
}

class _Button extends StatelessWidget {
  final IconData icon;
  final String tooltip;
  final bool active;
  final VoidCallback onTap;

  const _Button({
    required this.icon,
    required this.tooltip,
    required this.onTap,
    this.active = false,
  });

  @override
  Widget build(BuildContext context) {
    return Tooltip(
      message: tooltip,
      child: Material(
        color: Colors.transparent,
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(12),
          // 46px keeps this a comfortable click and touch target.
          child: SizedBox(
            width: 46,
            height: 46,
            child: Icon(icon, size: 22, color: active ? _accent : Colors.black87),
          ),
        ),
      ),
    );
  }
}
