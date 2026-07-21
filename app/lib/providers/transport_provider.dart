import 'dart:async';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../services/transport/apollo_transport.dart';
import '../services/transport/wifi_transport.dart';

class TransportNotifier extends Notifier<ApolloTransport?> {
  StreamSubscription? _stateSub;
  StreamSubscription? _eventSub;
  Timer? _pollTimer;
  String _lastHost = '127.0.0.1';
  int _lastPort = 8765;
  bool _autoConnecting = false;

  @override
  ApolloTransport? build() {
    // Auto-connect on startup
    Future.microtask(() => startAutoConnect());
    return null;
  }

  /// Start auto-connect with 200ms polling
  Future<void> startAutoConnect({String host = '127.0.0.1', int port = 8765}) async {
    _lastHost = host;
    _lastPort = port;
    _autoConnecting = true;

    // Cancel any existing poll timer
    _pollTimer?.cancel();

    // Try to connect immediately
    await _attemptConnect(host, port);

    // Poll every 200ms
    _pollTimer = Timer.periodic(const Duration(milliseconds: 200), (_) {
      _attemptConnect(host, port);
    });
  }

  /// Attempt connection without throwing
  Future<void> _attemptConnect(String host, int port) async {
    try {
      if (state == null) {
        await connectWifi(host, port);
      }
    } catch (e) {
      // Silent fail for polling
    }
  }

  Future<void> connectWifi(String host, int port) async {
    await _detach();
    final t = WifiTransport(host: host, port: port);
    state = t;
    _stateSub = t.stateStream.listen((_) => ref.notifyListeners());
    await t.connect();

    // Stop polling on successful connect
    if (_autoConnecting) {
      _pollTimer?.cancel();
      _autoConnecting = false;
    }
  }

  // BLE: caller creates BleTransport and passes it in
  Future<void> connectTransport(ApolloTransport t) async {
    await _detach();
    state = t;
    _stateSub = t.stateStream.listen((_) => ref.notifyListeners());
    await t.connect();
    _pollTimer?.cancel();
    _autoConnecting = false;
  }

  Future<void> disconnect() async {
    _pollTimer?.cancel();
    _autoConnecting = false;
    await _detach();
  }

  Stream<String>? get eventStream => state?.events;

  Future<void> _detach() async {
    await _stateSub?.cancel();
    await _eventSub?.cancel();
    await state?.disconnect();
    state = null;
  }
}

final transportProvider =
    NotifierProvider<TransportNotifier, ApolloTransport?>(TransportNotifier.new);
