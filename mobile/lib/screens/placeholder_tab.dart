import 'package:flutter/material.dart';

/// Simple stand-in for tabs whose screens aren't built yet.
class PlaceholderTab extends StatelessWidget {
  final String title;
  final IconData icon;

  const PlaceholderTab({super.key, required this.title, required this.icon});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFFF3F4F6),
      body: SafeArea(
        bottom: false,
        child: Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(icon, size: 56, color: Colors.grey.shade400),
              const SizedBox(height: 16),
              Text(
                title,
                style: TextStyle(
                  fontSize: 22,
                  fontWeight: FontWeight.w700,
                  color: Colors.grey.shade700,
                ),
              ),
              const SizedBox(height: 6),
              Text(
                'Coming soon',
                style: TextStyle(fontSize: 15, color: Colors.grey.shade500),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
