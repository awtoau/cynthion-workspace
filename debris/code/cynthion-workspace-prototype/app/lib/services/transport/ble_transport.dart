import 'dart:async';
import 'dart:convert';
import 'package:flutter_blue_plus/flutter_blue_plus.dart';
import 'apollo_transport.dart';

// Custom GATT UUIDs — awto-apollod service
const _serviceUuid    = '0000a770-0000-1000-8000-00805f9b34fb';
const _txCharUuid     = '0000a771-0000-1000-8000-00805f9b34fb'; // notify: apollod → phone
const _rxCharUuid     = '0000a772-0000-1000-8000-00805f9b34fb'; // write: phone → apollod

// BLE chunking: [seq:uint8][flags:uint8 bit0=last][payload bytes]
// Max payload per chunk = negotiated MTU - 3 (ATT header) - 2 (our header) = MTU-5

class BleTransport implements ApolloTransport {
  final BluetoothDevice device;

  BleTransport(this.device);

  BluetoothCharacteristic? _txChar;
  BluetoothCharacteristic? _rxChar;
  final _stateCtrl = StreamController<TransportState>.broadcast();
  final _eventCtrl = StreamController<String>.broadcast();
  StreamSubscription? _notifySub;
  TransportState _state = TransportState.disconnected;

  // Chunk reassembly buffer per sequence
  final Map<int, List<int>> _chunks = {};

  @override TransportKind get kind => TransportKind.ble;
  @override TransportState get state => _state;
  @override Stream<TransportState> get stateStream => _stateCtrl.stream;
  @override Stream<String> get events => _eventCtrl.stream;
  @override String get displayName =>
      'BLE  ${device.advName.isNotEmpty ? device.advName : device.remoteId.str}';

  @override
  Future<void> connect() async {
    _setState(TransportState.connecting);
    try {
      await device.connect(timeout: const Duration(seconds: 10));
      await device.requestMtu(512);

      final services = await device.discoverServices();
      final svc = services.firstWhere(
          (s) => s.serviceUuid.toString().toLowerCase() == _serviceUuid);
      _txChar = svc.characteristics
          .firstWhere((c) => c.characteristicUuid.toString().toLowerCase() == _txCharUuid);
      _rxChar = svc.characteristics
          .firstWhere((c) => c.characteristicUuid.toString().toLowerCase() == _rxCharUuid);

      await _txChar!.setNotifyValue(true);
      _notifySub = _txChar!.onValueReceived.listen(_onChunk);

      _setState(TransportState.connected);

      device.connectionState.listen((cs) {
        if (cs == BluetoothConnectionState.disconnected) {
          _setState(TransportState.disconnected);
        }
      });
    } catch (_) {
      _setState(TransportState.error);
      rethrow;
    }
  }

  @override
  Future<void> disconnect() async {
    await _notifySub?.cancel();
    await device.disconnect();
    _setState(TransportState.disconnected);
  }

  @override
  Future<void> send(String json) async {
    if (_rxChar == null) return;
    final bytes = utf8.encode('$json\n');
    // Write in ≤512 byte chunks (MTU - 3 ATT - 2 header)
    const chunkSize = 507;
    for (var i = 0; i < bytes.length; i += chunkSize) {
      final isLast = (i + chunkSize) >= bytes.length;
      final payload = bytes.sublist(i, isLast ? bytes.length : i + chunkSize);
      final pkt = [i ~/ chunkSize, isLast ? 1 : 0, ...payload];
      await _rxChar!.write(pkt, withoutResponse: true);
    }
  }

  void _onChunk(List<int> data) {
    if (data.length < 3) return;
    final seq    = data[0];
    final isLast = (data[1] & 0x01) != 0;
    final payload = data.sublist(2);

    _chunks.putIfAbsent(seq, () => []);
    if (seq == 0) {
      _chunks[0] = payload.toList();
    } else {
      _chunks[seq] = [...(_chunks[seq - 1] ?? []), ...payload];
    }

    if (isLast) {
      try {
        final line = utf8.decode(_chunks[seq]!).trim();
        if (line.isNotEmpty) _eventCtrl.add(line);
      } catch (_) {}
      _chunks.clear();
    }
  }

  void _setState(TransportState s) {
    _state = s;
    _stateCtrl.add(s);
  }

  /// Scan for nearby apollod BLE peripherals.
  static Stream<BluetoothDevice> scan({Duration timeout = const Duration(seconds: 10)}) async* {
    await FlutterBluePlus.startScan(
      withServices: [Guid(_serviceUuid)],
      timeout: timeout,
    );
    await for (final result in FlutterBluePlus.scanResults) {
      for (final r in result) {
        yield r.device;
      }
    }
  }
}
