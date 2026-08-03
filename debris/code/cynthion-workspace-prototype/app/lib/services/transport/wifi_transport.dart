import 'dart:async';
import 'package:web_socket_channel/web_socket_channel.dart';
import 'apollo_transport.dart';

class WifiTransport implements ApolloTransport {
  final String host;
  final int port;

  WifiTransport({required this.host, this.port = 7777});

  WebSocketChannel? _channel;
  final _stateCtrl = StreamController<TransportState>.broadcast();
  final _eventCtrl = StreamController<String>.broadcast();
  TransportState _state = TransportState.disconnected;

  @override TransportKind get kind => TransportKind.wifi;
  @override TransportState get state => _state;
  @override Stream<TransportState> get stateStream => _stateCtrl.stream;
  @override Stream<String> get events => _eventCtrl.stream;
  @override String get displayName => 'WiFi  $host:$port';

  @override
  Future<void> connect() async {
    _setState(TransportState.connecting);
    try {
      final uri = Uri.parse('ws://$host:$port');
      _channel = WebSocketChannel.connect(uri);
      await _channel!.ready;
      _setState(TransportState.connected);
      _channel!.stream.listen(
        (data) {
          if (data is String) {
            for (final line in data.split('\n')) {
              final t = line.trim();
              if (t.isNotEmpty) _eventCtrl.add(t);
            }
          }
        },
        onError: (_) => _setState(TransportState.error),
        onDone: () => _setState(TransportState.disconnected),
      );
    } catch (_) {
      _setState(TransportState.error);
      rethrow;
    }
  }

  @override
  Future<void> disconnect() async {
    await _channel?.sink.close();
    _channel = null;
    _setState(TransportState.disconnected);
  }

  @override
  Future<void> send(String json) async {
    _channel?.sink.add('$json\n');
  }

  void _setState(TransportState s) {
    _state = s;
    _stateCtrl.add(s);
  }
}
