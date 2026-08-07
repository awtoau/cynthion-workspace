import 'dart:async';
import 'dart:convert';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/device_profile.dart';
import '../services/transport/apollo_transport.dart';
import 'transport_provider.dart';

/// How long to wait for the hello before deciding the peer is not the awto
/// daemon.
///
/// The handshake is a single JSON line over a loopback WebSocket: the round
/// trip is well under a millisecond, and a daemon that has to open serial ports
/// first still answers inside a few hundred. A second is far longer than any
/// answer takes, so expiry means "this peer does not implement the hello",
/// which is exactly the conclusion drawn from it. It is not a retry window and
/// nothing is lost if a late hello arrives — it is still accepted, and the
/// profile upgrades in place.
const _helloGrace = Duration(seconds: 1);

/// Which device the GUI is talking to, and what it may therefore offer.
///
/// Asks on every connect and downgrades to [DeviceProfile.offline] on
/// disconnect, so a fork daemon that goes away cannot leave advanced panels
/// enabled against nothing.
class DeviceProfileNotifier extends Notifier<DeviceProfile> {
  StreamSubscription<String>? _events;
  StreamSubscription<TransportState>? _states;
  Timer? _grace;

  @override
  DeviceProfile build() {
    final transport = ref.watch(transportProvider);
    ref.onDispose(_detach);
    _detach();
    if (transport == null) return DeviceProfile.offline;

    _states = transport.stateStream.listen((s) {
      if (s == TransportState.connected) {
        _ask(transport);
      } else if (s != TransportState.connecting) {
        state = DeviceProfile.offline;
      }
    });
    _events = transport.events.listen(_onEvent);
    if (transport.state == TransportState.connected) _ask(transport);
    return DeviceProfile.offline;
  }

  void _ask(ApolloTransport transport) {
    _grace?.cancel();
    // A daemon that sends its hello unprompted answers this immediately; one
    // that only replies to a query gets the query.
    transport.send(jsonEncode({'cmd': 'hello'}));
    _grace = Timer(_helloGrace, () {
      if (state.variant == DeviceVariant.offline) {
        state = DeviceProfile.stock;
      }
    });
  }

  void _onEvent(String line) {
    Map<String, dynamic> json;
    try {
      final decoded = jsonDecode(line);
      if (decoded is! Map<String, dynamic>) return;
      json = decoded;
    } catch (_) {
      // Not JSON: a raw TTY line from a plain serial bridge. That is a peer
      // that speaks no protocol, which is precisely the stock case.
      if (state.variant == DeviceVariant.offline) {
        state = DeviceProfile.stock;
      }
      return;
    }
    final profile = DeviceProfile.fromHello(json);
    if (profile != null) {
      _grace?.cancel();
      state = profile;
    }
  }

  void _detach() {
    _grace?.cancel();
    _events?.cancel();
    _states?.cancel();
    _grace = null;
    _events = null;
    _states = null;
  }
}

final deviceProfileProvider =
    NotifierProvider<DeviceProfileNotifier, DeviceProfile>(
        DeviceProfileNotifier.new);

/// Whether a named capability may be used right now.
final capabilityProvider = Provider.family<bool, String>(
    (ref, cap) => ref.watch(deviceProfileProvider).has(cap));
