import 'dart:async';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../services/transport/apollo_transport.dart';
import '../services/transport/wifi_transport.dart';
import '../services/transport/mdns_discovery.dart';
import './device_capabilities_provider.dart';

class TransportNotifier extends Notifier<ApolloTransport?> {
  StreamSubscription? _stateSub;
  StreamSubscription? _eventSub;
  Timer? _autoConnectTimer;

  @override
  ApolloTransport? build() {
    _startAutoConnect();
    ref.onDispose(() => _autoConnectTimer?.cancel());
    return null;
  }

  void _startAutoConnect() {
    _autoConnectTimer = Timer.periodic(const Duration(milliseconds: 50), (_) {
      _tryAutoConnect();
    });
  }

  Future<void> _tryAutoConnect() async {
    if (state != null) return; // Already connected

    // Try both mDNS and localhost in parallel, connect to whichever responds first
    try {
      await Future.wait([
        _tryConnect('127.0.0.1', 7777),  // localhost (fast, always available)
        _discoverAndConnect(),            // mDNS (may find on-network instance)
      ], eagerError: false);
      // If any succeeded, state will be set and we'll return on next tick
    } catch (_) {
      // All attempts failed, will retry next tick
    }
  }

  Future<void> _discoverAndConnect() async {
    final discovered = await discoverApolloD(timeout: const Duration(milliseconds: 100));
    if (discovered.isNotEmpty) {
      final host = discovered.first;
      await _tryConnect(host.host, host.port);
    } else {
      throw Exception('No hosts discovered');
    }
  }

  Future<void> _tryConnect(String host, int port) async {
    final t = WifiTransport(host: host, port: port);
    await t.connect().timeout(const Duration(milliseconds: 200));
    // Only set state if not already connected (race condition safe)
    if (state == null) {
      state = t;
      _stateSub = t.stateStream.listen((_) {
        ref.notifyListeners();
        if (t.state == TransportState.connected) {
          _onConnected();
        }
      });
    }
  }

  Future<void> connectWifi(String host, int port) async {
    await _detach();
    final t = WifiTransport(host: host, port: port);
    state = t;
    _stateSub = t.stateStream.listen((_) {
      ref.notifyListeners();
      if (t.state == TransportState.connected) {
        _onConnected();
      }
    });
    await t.connect();
  }

  // BLE: caller creates BleTransport and passes it in
  Future<void> connectTransport(ApolloTransport t) async {
    await _detach();
    state = t;
    _stateSub = t.stateStream.listen((_) {
      ref.notifyListeners();
      if (t.state == TransportState.connected) {
        _onConnected();
      }
    });
    await t.connect();
  }

  void _onConnected() {
    ref.read(deviceCapabilitiesProvider.notifier).detectCapabilities();
  }

  Future<void> disconnect() async => _detach();

  Future<void> shutdown() async {
    if (state != null) {
      try {
        await state!.send('{"method": "shutdown"}');
        await Future.delayed(const Duration(milliseconds: 500));
      } catch (_) {}
    }
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
