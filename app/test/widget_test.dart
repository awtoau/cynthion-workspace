import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:cynthion_monitor/main.dart';

void main() {
  testWidgets('app smoke test', (WidgetTester tester) async {
    await tester.pumpWidget(ProviderScope(child: CynthionMonitorApp()));
    expect(find.text('Cynthion Monitor'), findsOneWidget);
  });
}
