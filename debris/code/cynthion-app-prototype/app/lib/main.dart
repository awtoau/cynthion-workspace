import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'theme.dart';
import 'screens/main_screen.dart';

void main() {
  runApp(const ProviderScope(child: CynthionMonitorApp()));
}

class CynthionMonitorApp extends StatelessWidget {
  const CynthionMonitorApp({super.key});

  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'Cynthion Monitor',
        theme: buildTheme(),
        debugShowCheckedModeBanner: false,
        home: const MainScreen(),
      );
}
