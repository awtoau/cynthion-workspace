import 'dart:async';
import 'dart:math';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../models/tty_line.dart';

const _maxLines = 500;

// Stub: emits fake heartbeat lines so the UI has something to show.
// Replace _startStub() with a Unix socket reader once apollod IPC is wired.
class TtyNotifier extends Notifier<List<TtyLine>> {
  Timer? _timer;
  int _tick = 0;

  @override
  List<TtyLine> build() {
    _startStub();
    ref.onDispose(() => _timer?.cancel());
    return [];
  }

  void append(TtyLine line) {
    final next = [...state, line];
    state = next.length > _maxLines ? next.sublist(next.length - _maxLines) : next;
  }

  void _startStub() {
    final rng = Random();
    _timer = Timer.periodic(const Duration(milliseconds: 800), (_) {
      _tick++;
      final sources = TtySource.values.where((s) => s != TtySource.system).toList();
      final src = sources[_tick % sources.length];
      final isFault = rng.nextInt(30) == 0;
      final text = isFault
          ? '⚠ DEMO FAULT — stack_canary=0x${rng.nextInt(0xFFFF).toRadixString(16).padLeft(4, '0')} [FAKE DATA]'
          : switch (src) {
              TtySource.rv0 => '♥ heartbeat tick=$_tick  uptime=${_tick * 100}ms [FAKE]',
              TtySource.fpg => 'evt endpoint=${rng.nextInt(8)} pkt_len=${rng.nextInt(512)} [FAKE]',
              TtySource.apl => '> apollo ready  cpu=${rng.nextInt(100)}% [FAKE]',
              TtySource.system => '',
            };
      append(TtyLine(
        source: src,
        timestamp: DateTime.now(),
        text: text,
        isFault: isFault,
      ));
    });
  }
}

final ttyProvider = NotifierProvider<TtyNotifier, List<TtyLine>>(TtyNotifier.new);
