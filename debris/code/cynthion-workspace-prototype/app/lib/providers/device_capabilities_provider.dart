import 'dart:async';
import 'dart:convert';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../models/device_capabilities.dart';
import './transport_provider.dart';

class DeviceCapabilitiesNotifier extends Notifier<DeviceCapabilities> {
  StreamSubscription? _eventSub;

  @override
  DeviceCapabilities build() {
    ref.onDispose(() => _eventSub?.cancel());
    return DeviceCapabilities.vendorDefault();
  }

  Future<void> detectCapabilities() async {
    final transport = ref.read(transportProvider);
    if (transport == null) {
      state = DeviceCapabilities.stockDefault();
      return;
    }

    _eventSub?.cancel();
    final completer = Completer<DeviceCapabilities>();

    try {
      _eventSub = transport.events.listen((event) {
        if (!completer.isCompleted) {
          try {
            final data = jsonDecode(event) as Map<String, dynamic>;

            if (data['type'] == 'device_info' || data['method'] == 'get_device_info') {
              final caps = DeviceCapabilities.fromJson(data);
              completer.complete(caps);
              state = caps;
            }
          } catch (_) {}
        }
      });

      await transport.send(jsonEncode({'method': 'get_device_info'}));

      final caps = await completer.future.timeout(
        const Duration(seconds: 1),
        onTimeout: () => DeviceCapabilities.vendorDefault(),
      );

      state = caps;
    } catch (_) {
      state = DeviceCapabilities.vendorDefault();
    }
  }
}

final deviceCapabilitiesProvider = NotifierProvider<DeviceCapabilitiesNotifier, DeviceCapabilities>(
  DeviceCapabilitiesNotifier.new,
);
