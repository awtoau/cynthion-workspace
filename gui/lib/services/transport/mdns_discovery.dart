import 'dart:async';
import 'package:multicast_dns/multicast_dns.dart';

class DiscoveredHost {
  final String name;
  final String host;
  final int port;
  const DiscoveredHost({required this.name, required this.host, required this.port});
}

/// Scans the local network for apollod instances advertising _apollod._tcp.local
Future<List<DiscoveredHost>> discoverApolloD({
  Duration timeout = const Duration(seconds: 4),
}) async {
  final client = MDnsClient();
  final found = <DiscoveredHost>[];

  await client.start();
  try {
    await for (final ptr in client
        .lookup<PtrResourceRecord>(
            ResourceRecordQuery.serverPointer('_apollod._tcp.local'))
        .timeout(timeout, onTimeout: (_) {})) {
      await for (final srv in client
          .lookup<SrvResourceRecord>(
              ResourceRecordQuery.service(ptr.domainName))
          .timeout(const Duration(seconds: 2), onTimeout: (_) {})) {
        await for (final ip in client
            .lookup<IPAddressResourceRecord>(
                ResourceRecordQuery.addressIPv4(srv.target))
            .timeout(const Duration(seconds: 2), onTimeout: (_) {})) {
          found.add(DiscoveredHost(
            name: ptr.domainName,
            host: ip.address.address,
            port: srv.port,
          ));
        }
      }
    }
  } finally {
    client.stop();
  }
  return found;
}
