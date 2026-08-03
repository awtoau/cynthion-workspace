import 'dart:async';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:window_manager/window_manager.dart';
import 'package:awto_gui_inspect/awto_gui_inspect.dart';
import 'providers/transport_provider.dart';
import 'providers/inspect_provider.dart';
import 'services/transport/apollo_transport.dart';
import 'theme.dart';
import 'screens/main_screen.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await windowManager.ensureInitialized();

  const defaultSize = Size(1400, 900);
  final prefs = await SharedPreferences.getInstance();
  final raw = prefs.getString('window_geometry_v1');

  Offset? savedPos;
  Size savedSize = defaultSize;
  if (raw != null) {
    try {
      final parts = raw.split(',').map(double.parse).toList();
      if (parts.length == 4) {
        savedPos = Offset(parts[0], parts[1]);
        savedSize = Size(parts[2], parts[3]);
      }
    } catch (_) {}
  }

  await windowManager.waitUntilReadyToShow(
    WindowOptions(
      size: savedSize,
      minimumSize: const Size(900, 600),
      title: 'Cynthion Monitor',
      backgroundColor: Colors.transparent,
      skipTaskbar: false,
      titleBarStyle: TitleBarStyle.normal,
    ),
    () async {
      if (savedPos != null) {
        await windowManager.setPosition(savedPos);
      } else {
        await windowManager.center();
      }
      await windowManager.show();
      await windowManager.focus();
    },
  );

  runApp(ProviderScope(child: CynthionMonitorApp()));
}

class CynthionMonitorApp extends ConsumerWidget {
  late final AwtoAiLogHistory _logHistory;

  CynthionMonitorApp({super.key}) {
    _logHistory = AwtoAiLogHistory(inner: AwtoConsoleAiLogSink());
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final inspectEnabled = ref.watch(inspectProvider);

    final home = inspectEnabled
        ? AwtoInspectApp(
            enabled: true,
            screenName: 'CynthionMonitor',
            aiLogSink: _logHistory,
            child: _AppShell(child: MainScreen()),
          )
        : _AppShell(child: MainScreen());

    return MaterialApp(
      title: 'Cynthion Monitor',
      theme: buildTheme(),
      debugShowCheckedModeBanner: false,
      home: home,
    );
  }
}

// Saves window geometry and handles graceful shutdown (Ctrl+Q / window close).
class _AppShell extends ConsumerStatefulWidget {
  final Widget child;
  const _AppShell({required this.child});

  @override
  ConsumerState<_AppShell> createState() => _AppShellState();
}

class _AppShellState extends ConsumerState<_AppShell> with WindowListener {
  Timer? _debounce;

  @override
  void initState() {
    super.initState();
    windowManager.addListener(this);
    windowManager.setPreventClose(true);
  }

  @override
  void dispose() {
    _debounce?.cancel();
    windowManager.removeListener(this);
    super.dispose();
  }

  // ── Geometry save ──────────────────────────────────────────────────────────
  void _scheduleGeometrySave() {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 800), _saveGeometry);
  }

  Future<void> _saveGeometry() async {
    final pos = await windowManager.getPosition();
    final size = await windowManager.getSize();
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(
      'window_geometry_v1',
      '${pos.dx},${pos.dy},${size.width},${size.height}',
    );
  }

  @override
  void onWindowMove() => _scheduleGeometrySave();

  @override
  void onWindowResize() => _scheduleGeometrySave();

  // ── Graceful shutdown ──────────────────────────────────────────────────────
  Future<void> _doShutdown() async {
    final transport = ref.read(transportProvider);
    if (transport != null && transport.state == TransportState.connected) {
      try {
        await transport.send('{"cmd":"shutdown"}');
        // Give apollod a moment to flush TTYs and release locks.
        await Future.delayed(const Duration(milliseconds: 300));
      } catch (_) {}
    }
    exit(0);
  }

  @override
  Future<void> onWindowClose() => _doShutdown();

  @override
  Widget build(BuildContext context) => CallbackShortcuts(
        bindings: {
          const SingleActivator(LogicalKeyboardKey.keyQ, control: true):
              _doShutdown,
        },
        child: Focus(autofocus: true, child: widget.child),
      );
}
