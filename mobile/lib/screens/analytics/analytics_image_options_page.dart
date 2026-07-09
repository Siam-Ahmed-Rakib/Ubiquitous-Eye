import 'package:flutter/material.dart';

import '../../models/analytics_service.dart';
import '../../util/responsive.dart';
import '../area_selection_screen.dart';

const Color _ink = Color(0xFF0E1116);

/// Shown after ordering an analytics product. These products don't need an
/// image purchase, so the only step left is choosing where to run them.
class AnalyticsImageOptionsPage extends StatelessWidget {
  final AnalyticsService service;

  const AnalyticsImageOptionsPage({super.key, required this.service});

  void _selectArea(BuildContext context) {
    Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => AreaSelectionScreen(
          showBackButton: true,
          service: service,
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFFF3F4F6),
      appBar: AppBar(
        backgroundColor: Colors.white,
        surfaceTintColor: Colors.white,
        elevation: 0.5,
        centerTitle: true,
        foregroundColor: _ink,
        leading: IconButton(
          icon: const Icon(Icons.arrow_back),
          onPressed: () => Navigator.of(context).maybePop(),
        ),
        title: const Text(
          'Image Options',
          style: TextStyle(fontWeight: FontWeight.w700, fontSize: 18),
        ),
      ),
      body: ResponsiveCenter(
        maxWidth: 640,
        padding: const EdgeInsets.fromLTRB(20, 28, 20, 0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
              'This analytics product does not require an image purchase to be '
              'completed.',
              style: TextStyle(
                fontSize: 18,
                height: 1.4,
                color: Colors.grey.shade800,
              ),
            ),
            const SizedBox(height: 28),
            SizedBox(
              height: 60,
              child: OutlinedButton(
                onPressed: () => _selectArea(context),
                style: OutlinedButton.styleFrom(
                  foregroundColor: _ink,
                  side: const BorderSide(color: _ink, width: 1.4),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(10),
                  ),
                ),
                child: const Text(
                  'SELECT YOUR AREA OF INTEREST',
                  style: TextStyle(
                    fontSize: 15,
                    fontWeight: FontWeight.w700,
                    letterSpacing: 0.5,
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
