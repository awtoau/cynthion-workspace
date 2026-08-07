import 'dart:async';

enum TransportKind { unixSocket, wifi, ble }

enum TransportState { disconnected, connecting, connected, error }

abstract class ApolloTransport {
  TransportKind get kind;
  TransportState get state;
  Stream<TransportState> get stateStream;

  /// JSON-line events from apollod
  Stream<String> get events;

  /// Send a command to apollod (JSON line)
  Future<void> send(String json);

  Future<void> connect();
  Future<void> disconnect();

  String get displayName;
}
