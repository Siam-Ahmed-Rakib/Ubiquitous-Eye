import 'package:flutter/material.dart';

import '../util/responsive.dart';

const Color _ink = Color(0xFF0E1116);
const Color _accent = Color(0xFFEF9A3D);

/// The Ubiquitous Eyes wordmark: a hand-drawn iris/aperture eye mark beside a
/// tracked wordmark with "EYES" in the brand accent, and an ANALYTICS eyebrow.
class UbiquitousEyesLogo extends StatelessWidget {
  final bool showAnalyticsLabel;

  const UbiquitousEyesLogo({super.key, this.showAnalyticsLabel = true});

  @override
  Widget build(BuildContext context) {
    // Grow the wordmark on tablets / wide web so it doesn't look lost in the
    // extra header space.
    final scale = context.responsive(phone: 1.0, tablet: 1.18, desktop: 1.3);
    final eyeSize = 30.0 * scale;

    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            CustomPaint(
              size: Size(eyeSize, eyeSize),
              painter: const _IrisEyePainter(),
            ),
            SizedBox(width: 11 * scale),
            RichText(
              text: TextSpan(
                style: TextStyle(
                  fontSize: 21 * scale,
                  fontWeight: FontWeight.w800,
                  letterSpacing: 2.0 * scale,
                  color: _ink,
                ),
                children: const [
                  TextSpan(text: 'UBIQUITOUS '),
                  TextSpan(text: 'EYES', style: TextStyle(color: _accent)),
                ],
              ),
            ),
          ],
        ),
        if (showAnalyticsLabel) ...[
          SizedBox(height: 7 * scale),
          Text(
            'A N A L Y T I C S',
            style: TextStyle(
              fontSize: 11 * scale,
              letterSpacing: 5.5 * scale,
              fontWeight: FontWeight.w500,
              color: Colors.grey.shade500,
            ),
          ),
        ],
      ],
    );
  }
}

class _IrisEyePainter extends CustomPainter {
  const _IrisEyePainter();

  @override
  void paint(Canvas canvas, Size size) {
    final w = size.width;
    final h = size.height;
    final c = Offset(w / 2, h / 2);

    final ink = Paint()
      ..color = _ink
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.7
      ..strokeCap = StrokeCap.round;

    // Eye almond outline (two opposing arcs).
    final eye = Path()
      ..moveTo(0.04 * w, c.dy)
      ..quadraticBezierTo(c.dx, 0.06 * h, 0.96 * w, c.dy)
      ..quadraticBezierTo(c.dx, 0.94 * h, 0.04 * w, c.dy)
      ..close();
    canvas.drawPath(eye, ink);

    // Iris.
    canvas.drawCircle(c, 0.26 * w, Paint()..color = _accent);
    canvas.drawCircle(c, 0.26 * w, ink..strokeWidth = 1.4);

    // Pupil + catch-light.
    canvas.drawCircle(c, 0.10 * w, Paint()..color = _ink);
    canvas.drawCircle(
      c + Offset(-0.055 * w, -0.06 * h),
      0.03 * w,
      Paint()..color = Colors.white,
    );
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}
