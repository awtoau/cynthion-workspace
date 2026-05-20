import 'dart:async';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../services/transport/apollo_transport.dart';
import '../services/transport/wifi_transport.dart';

class TransportNotifier extends Notifier<ApolloTransport?> {
  StreamSubscription? _stateSub;
  StreamSubscription? _eventSub;

  @override
  ApolloTransport? build() => null;

  Future<void> connectWifi(String host, int port) async {
    await _detach();
    final t = WifiTransport(host: host, port: port);
    state = t;
    _stateSub = t.stateStream.listen((_) => ref.notifyListeners());
    await t.connect();
  }

  // BLE: caller creates BleTransport and passes it in
  Future<void> connectTransport(ApolloTransport t) async {
    await _detach();
    state = t;
    _stateSub = t.stateStream.listen((_) => ref.notifyListeners());
    await t.connect();
  }

  Future<void> disconnect() async => _detach();

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
