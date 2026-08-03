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
    _timer = Timer.periodic(const Duration(milliseconds: 300), (_) {
      _tick++;

      // Cycle through sources with varied timing
      TtySource src = switch (_tick % 12) {
        0 || 4 || 8 => TtySource.rv0,
        1 || 5 || 9 => TtySource.fpg,
        2 || 6 || 10 => TtySource.apl,
        _ => TtySource.system,
      };

      // Occasional faults
      final isFault = rng.nextInt(50) == 0;

      final text = isFault
          ? '⚠ fault: canary=0x${rng.nextInt(0xFFFF).toRadixString(16).padLeft(4, '0')} exception_pc=0x${rng.nextInt(0xFFFFFF).toRadixString(16).padLeft(6, '0')}'
          : switch (src) {
              TtySource.rv0 => '♥ heartbeat uptime=${_tick * 100}ms clk=${rng.nextInt(500)}MHz rv0_state=${["READY", "IDLE", "ACTIVE"][_tick % 3]}',
              TtySource.fpg => 'evt ep=${rng.nextInt(8)} len=${64 + rng.nextInt(448)} pkt_id=${_tick ~/ 3} crc=${(rng.nextInt(0xFFFF)).toRadixString(16).padLeft(4, '0')}',
              TtySource.apl => 'apollo dbg: jtag_ir=0x${rng.nextInt(0xFF).toRadixString(16).padLeft(2, '0')} tap_state=${["RTI", "SHIFT_DR", "UPDATE_DR"][_tick % 3]} freq=${_tick * 10 % 25}MHz',
              TtySource.system => 'sys: load_avg=${(rng.nextDouble() * 2).toStringAsFixed(2)} memory=${rng.nextInt(80)}% [${["idle", "scan", "xfer", "wait"][_tick % 4]}]',
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
