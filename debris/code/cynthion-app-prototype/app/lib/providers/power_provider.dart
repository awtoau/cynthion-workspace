import 'dart:async';
import 'dart:math';
import 'package:flutter_riverpod/flutter_riverpod.dart';

class PowerChannel {
  final String label;
  final double voltage;
  final double current;
  double get power => voltage * current;

  const PowerChannel({
    required this.label,
    required this.voltage,
    required this.current,
  });
}

// Stub: fake PAC1954 readings. Replace with apollod stream once wired.
class PowerNotifier extends Notifier<List<PowerChannel>> {
  Timer? _timer;
  final _rng = Random();

  @override
  List<PowerChannel> build() {
    _startStub();
    ref.onDispose(() => _timer?.cancel());
    return _fakeChannels(0);
  }

  List<PowerChannel> _fakeChannels(int tick) => [
        PowerChannel(label: 'VBUS 5V',  voltage: 5.0  + _jitter(0.05), current: 0.48 + _jitter(0.03)),
        PowerChannel(label: 'VCC 3V3',  voltage: 3.3  + _jitter(0.02), current: 0.11 + _jitter(0.01)),
        PowerChannel(label: 'VCCIO 1V8',voltage: 1.8  + _jitter(0.01), current: 0.06 + _jitter(0.005)),
        PowerChannel(label: 'FPGA VCC', voltage: 1.1  + _jitter(0.01), current: 0.22 + _jitter(0.02)),
      ];

  double _jitter(double scale) => (_rng.nextDouble() - 0.5) * scale * 2;

  void _startStub() {
    _timer = Timer.periodic(const Duration(milliseconds: 500), (_) {
      state = _fakeChannels(_timer.hashCode);
    });
  }
}

final powerProvider =
    NotifierProvider<PowerNotifier, List<PowerChannel>>(PowerNotifier.new);
