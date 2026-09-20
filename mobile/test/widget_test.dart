import 'package:flutter_test/flutter_test.dart';

import 'package:ubiquitous_eye/main.dart';

void main() {
  testWidgets('Home page renders search bar and continue button', (tester) async {
    await tester.pumpWidget(const UbiquitousEyeApp());

    
    expect(find.text('Search for place or coordinates'), findsOneWidget);
    // The map opens in tap-a-corner mode with nothing selected, so the action
    // button prompts for corners until an area exists.
    expect(find.text('TAP 4 MORE CORNERS'), findsOneWidget);
  });
}
