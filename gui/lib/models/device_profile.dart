/// What the thing on the other end of the transport can actually do.
///
/// The GUI grew up against the awto fork, where a daemon streams three TTYs and
/// PAC1954 rail readings. A stock Cynthion has none of that: there is no daemon
/// to connect to, and the vendor tools talk to the board over USB from Python.
/// Rather than assume the fork, the GUI asks and then only offers what the
/// answer supports.
library;

/// Capability names, as sent by the daemon in its hello frame.
///
/// Kept as constants rather than an enum because the wire format is a list of
/// strings and a future daemon may advertise names this build has never heard
/// of — those are carried through and simply never asked about.
class Cap {
  static const tty = 'tty'; // per-source console streams
  static const power = 'power'; // PAC1954 rail measurements
  static const topology = 'topology'; // live node/link status
  static const riscv = 'riscv'; // RISC-V core console and control
  static const gateware = 'gateware'; // bitstream load/reload
  static const shutdown = 'shutdown'; // graceful daemon shutdown

  /// Everything the awto fork's daemon is expected to offer. Used only as the
  /// assumption for a daemon that answers the hello but lists nothing.
  static const awtoDefaults = {tty, power, topology, riscv, shutdown};
}

enum DeviceVariant {
  /// Nothing is connected. Panels show demo data, clearly marked.
  offline,

  /// Something answered but did not identify itself as the awto daemon: a
  /// stock Cynthion behind a plain bridge, or an older daemon.
  stock,

  /// The awto fork's daemon, which named itself and its capabilities.
  awto,
}

class DeviceProfile {
  final DeviceVariant variant;
  final String version;
  final String board;
  final Set<String> caps;

  const DeviceProfile({
    required this.variant,
    this.version = '',
    this.board = '',
    this.caps = const {},
  });

  static const offline = DeviceProfile(variant: DeviceVariant.offline);

  /// A peer that connected but never answered the hello. It exists, so it is
  /// not offline; it did not identify itself, so nothing advanced is offered.
  static const stock = DeviceProfile(variant: DeviceVariant.stock);

  bool has(String cap) => caps.contains(cap);

  /// True while the panel should show generated data instead of measurements.
  bool get isDemo => variant == DeviceVariant.offline;

  String get label => switch (variant) {
        DeviceVariant.offline => 'demo data · no device',
        DeviceVariant.stock => 'stock Cynthion${version.isEmpty ? '' : ' $version'}',
        DeviceVariant.awto => 'awto fork${version.isEmpty ? '' : ' $version'}',
      };

  /// Parses the daemon's hello frame:
  ///
  /// ```json
  /// {"evt":"hello","variant":"awto","version":"0.3.1","board":"cynthion r1.4",
  ///  "caps":["tty","power","topology","riscv"]}
  /// ```
  ///
  /// Anything that parses but omits `variant` is treated as stock: a peer that
  /// does not claim the fork does not get the fork's features.
  static DeviceProfile? fromHello(Map<String, dynamic> j) {
    if (j['evt'] != 'hello' && j['type'] != 'hello') return null;
    final variant = (j['variant'] as String?)?.toLowerCase();
    final caps = (j['caps'] as List?)?.map((e) => e.toString()).toSet();
    if (variant == 'awto') {
      return DeviceProfile(
        variant: DeviceVariant.awto,
        version: j['version'] as String? ?? '',
        board: j['board'] as String? ?? '',
        caps: caps ?? Cap.awtoDefaults,
      );
    }
    return DeviceProfile(
      variant: DeviceVariant.stock,
      version: j['version'] as String? ?? '',
      board: j['board'] as String? ?? '',
      caps: caps ?? const {},
    );
  }
}
