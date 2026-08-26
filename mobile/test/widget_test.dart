import 'package:flutter_test/flutter_test.dart';

import 'package:ubiquitous_eye/main.dart';

void main() {
  testWidgets('Home page renders search bar and continue button', (tester) async {
    await tester.pumpWidget(const UbiquitousEyeApp());

    
    expect(find.text('Search for place or coordinates'), findsOneWidget);
    expect(find.text('CONTINUE TO OPTIONS'), findsOneWidget);
  });
}
