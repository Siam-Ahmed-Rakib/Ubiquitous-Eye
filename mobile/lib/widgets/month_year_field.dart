import 'package:flutter/material.dart';

const List<String> _months = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

/// A labelled year + month picker pair for one Sentinel-2 composite date.
///
/// Years start at 2017, the first full year of Sentinel-2 L2A coverage.
class MonthYearField extends StatelessWidget {
  final String label;
  final int year;
  final int month;
  final ValueChanged<int> onYear;
  final ValueChanged<int> onMonth;

  const MonthYearField({
    super.key,
    required this.label,
    required this.year,
    required this.month,
    required this.onYear,
    required this.onMonth,
  });

  @override
  Widget build(BuildContext context) {
    final currentYear = DateTime.now().year;
    final years = [for (var y = currentYear; y >= 2017; y--) y];

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          label,
          style: TextStyle(
            fontSize: 12,
            fontWeight: FontWeight.w600,
            color: Colors.grey.shade700,
          ),
        ),
        const SizedBox(height: 6),
        Row(
          children: [
            Expanded(
              child: _dropdown<int>(
                value: year,
                items: [
                  for (final y in years)
                    DropdownMenuItem(value: y, child: Text('$y')),
                ],
                onChanged: (v) => v != null ? onYear(v) : null,
              ),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: _dropdown<int>(
                value: month,
                items: [
                  for (var m = 1; m <= 12; m++)
                    DropdownMenuItem(value: m, child: Text(_months[m - 1])),
                ],
                onChanged: (v) => v != null ? onMonth(v) : null,
              ),
            ),
          ],
        ),
      ],
    );
  }

  Widget _dropdown<T>({
    required T value,
    required List<DropdownMenuItem<T>> items,
    required ValueChanged<T?> onChanged,
  }) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12),
      decoration: BoxDecoration(
        color: const Color(0xFFF3F4F6),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: Colors.grey.shade300),
      ),
      child: DropdownButtonHideUnderline(
        child: DropdownButton<T>(
          value: value,
          isExpanded: true,
          items: items,
          onChanged: onChanged,
        ),
      ),
    );
  }
}
