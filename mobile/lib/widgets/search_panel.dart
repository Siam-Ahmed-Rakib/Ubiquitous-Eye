import 'dart:async';

import 'package:flutter/material.dart';
import 'package:latlong2/latlong.dart';

import '../services/geocoding_service.dart';

const Color _accent = Color(0xFFEF9A3D);

/// The top search bar plus its results dropdown.
///
/// Self-contained: owns the text field, a debounce timer and the result
/// list, and simply reports the chosen location upward. [trailing] sits to the
/// right of the field (used for the layers / reset-box controls).
class SearchPanel extends StatefulWidget {
  final ValueChanged<LatLng> onLocationSelected;
  final Widget? trailing;

  const SearchPanel({
    super.key,
    required this.onLocationSelected,
    this.trailing,
  });

  @override
  State<SearchPanel> createState() => _SearchPanelState();
}

class _SearchPanelState extends State<SearchPanel> {
  final TextEditingController _controller = TextEditingController();
  final FocusNode _focus = FocusNode();
  final GeocodingService _service = GeocodingService();

  Timer? _debounce;
  List<PlaceResult> _results = const [];
  bool _loading = false;
  String? _error;

  @override
  void dispose() {
    _debounce?.cancel();
    _controller.dispose();
    _focus.dispose();
    super.dispose();
  }

  void _onChanged(String value) {
    _debounce?.cancel();
    if (value.trim().length < 2) {
      setState(() {
        _results = const [];
        _error = null;
        _loading = false;
      });
      return;
    }
    setState(() => _loading = true);
    _debounce = Timer(const Duration(milliseconds: 450), () => _runSearch(value));
  }

  Future<void> _runSearch(String query) async {
    try {
      final results = await _service.search(query);
      if (!mounted) return;
      setState(() {
        _results = results;
        _loading = false;
        _error = results.isEmpty ? 'No results found' : null;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _results = const [];
        _loading = false;
        _error = 'Search failed. Check your connection.';
      });
    }
  }

  void _select(PlaceResult result) {
    _controller.text = result.shortName;
    setState(() {
      _results = const [];
      _error = null;
    });
    _focus.unfocus();
    widget.onLocationSelected(result.location);
  }

  void _clear() {
    _controller.clear();
    setState(() {
      _results = const [];
      _error = null;
      _loading = false;
    });
  }

  @override
  Widget build(BuildContext context) {
    final hasOverlay = _loading || _error != null || _results.isNotEmpty;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            Expanded(child: _buildField()),
            if (widget.trailing != null) ...[
              const SizedBox(width: 8),
              widget.trailing!,
            ],
          ],
        ),
        if (hasOverlay)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: _buildResults(),
          ),
      ],
    );
  }

  Widget _buildField() {
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
        children: [
          const SizedBox(width: 14),
          const Icon(Icons.search, color: Colors.black54),
          const SizedBox(width: 8),
          Expanded(
            child: TextField(
              controller: _controller,
              focusNode: _focus,
              onChanged: _onChanged,
              textInputAction: TextInputAction.search,
              onSubmitted: (v) {
                _debounce?.cancel();
                if (v.trim().length >= 2) {
                  setState(() => _loading = true);
                  _runSearch(v);
                }
              },
              decoration: const InputDecoration(
                hintText: 'Search for place or coordinates',
                hintStyle: TextStyle(color: Colors.black45),
                border: InputBorder.none,
                isCollapsed: true,
              ),
              style: const TextStyle(color: Colors.black87, fontSize: 15),
            ),
          ),
          if (_loading)
            const Padding(
              padding: EdgeInsets.all(14),
              child: SizedBox(
                width: 18,
                height: 18,
                child: CircularProgressIndicator(strokeWidth: 2),
              ),
            )
          else if (_controller.text.isNotEmpty)
            IconButton(
              icon: const Icon(Icons.close, color: Colors.black45),
              onPressed: _clear,
            )
          else
            const SizedBox(width: 8),
        ],
      ),
    );
  }

  Widget _buildResults() {
    // Let the dropdown grow with the viewport instead of a fixed 280 px, so it
    // uses roomier tablet/desktop screens without overflowing short ones.
    final maxHeight =
        (MediaQuery.sizeOf(context).height * 0.5).clamp(220.0, 460.0);
    return Material(
      elevation: 6,
      borderRadius: BorderRadius.circular(12),
      clipBehavior: Clip.antiAlias,
      child: ConstrainedBox(
        constraints: BoxConstraints(maxHeight: maxHeight),
        child: _error != null
            ? Padding(
                padding: const EdgeInsets.all(16),
                child: Row(
                  children: [
                    const Icon(Icons.info_outline, size: 18, color: Colors.black45),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(_error!, style: const TextStyle(color: Colors.black54)),
                    ),
                  ],
                ),
              )
            : ListView.separated(
                shrinkWrap: true,
                padding: EdgeInsets.zero,
                itemCount: _results.length,
                separatorBuilder: (_, __) => const Divider(height: 1),
                itemBuilder: (context, i) {
                  final r = _results[i];
                  return ListTile(
                    dense: true,
                    leading: const Icon(Icons.place_outlined, color: _accent),
                    title: Text(
                      r.shortName,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(fontWeight: FontWeight.w600),
                    ),
                    subtitle: Text(
                      r.name,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(fontSize: 12),
                    ),
                    onTap: () => _select(r),
                  );
                },
              ),
      ),
    );
  }
}
