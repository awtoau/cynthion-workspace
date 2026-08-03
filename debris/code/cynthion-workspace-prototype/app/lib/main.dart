import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'dart:io';
import 'theme.dart';
import 'screens/main_screen.dart';
import 'providers/transport_provider.dart';

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
        home: const _ShutdownHandler(child: MainScreen()),
      );
}

class _ShutdownHandler extends ConsumerWidget {
  final Widget child;
  const _ShutdownHandler({required this.child});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Focus(
      onKeyEvent: (node, event) {
        if (event is KeyDownEvent) {
          final keyboard = HardwareKeyboard.instance;
          final isCtrlQ = keyboard.isControlPressed && event.logicalKey == LogicalKeyboardKey.keyQ;
          final isCmdQ = keyboard.isMetaPressed && event.logicalKey == LogicalKeyboardKey.keyQ;

          if (isCtrlQ || isCmdQ) {
            _shutdown(ref);
            return KeyEventResult.handled;
          }
        }
        return KeyEventResult.ignored;
      },
      autofocus: true,
      child: PopScope(
        canPop: false,
        onPopInvokedWithResult: (didPop, result) async {
          await _shutdown(ref);
        },
        child: child,
      ),
    );
  }

  Future<void> _shutdown(WidgetRef ref) async {
    await ref.read(transportProvider.notifier).shutdown();
    exit(0);
  }
}
