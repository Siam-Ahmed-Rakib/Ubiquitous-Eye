import 'package:flutter/material.dart';

import 'screens/main_scaffold.dart';

void main() => runApp(const TerraScopeApp());

/// App accent — the orange used across the SkyFi-style UI (nav bar, badges).
const Color kAccent = Color(0xFFEF9A3D);

class TerraScopeApp extends StatelessWidget {
  const TerraScopeApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'TerraScope',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        useMaterial3: true,
        colorScheme: ColorScheme.fromSeed(
          seedColor: kAccent,
          primary: kAccent,
        ),
        scaffoldBackgroundColor: Colors.black,
      ),
      // Clamp OS text scaling so very large accessibility font sizes can't
      // overflow the app's fixed-height controls (search bar, buttons, chips).
      builder: (context, child) {
        final mq = MediaQuery.of(context);
        return MediaQuery(
          data: mq.copyWith(
            textScaler: mq.textScaler.clamp(
              minScaleFactor: 1.0,
              maxScaleFactor: 1.3,
            ),
          ),
          child: child!,
        );
      },
      home: const MainScaffold(),
    );
  }
}
