import 'package:flutter_test/flutter_test.dart';

import 'package:terrascope/main.dart';

void main() {
  testWidgets('Home page renders search bar and continue button', (tester) async {
    await tester.pumpWidget(const TerraScopeApp());

    
    expect(find.text('Search for place or coordinates'), findsOneWidget);
    expect(find.text('CONTINUE TO OPTIONS'), findsOneWidget);
  });
}
