import 'package:flutter/material.dart';
import 'models/node.dart';

// ── App colours ──────────────────────────────────────────────────────────────
const bgCanvas    = Color(0xFF0D1117);
const bgPanel     = Color(0xFF161B22);
const bgCard      = Color(0xFF21262D);
const borderColor = Color(0xFF30363D);
const textPrimary = Color(0xFFE6EDF3);
const textMuted   = Color(0xFF8B949E);

// Hardware node accent colours
const colHost    = Color(0xFF58A6FF); // blue
const colApollo  = Color(0xFF3FB950); // green
const colFpga    = Color(0xFFF78166); // coral
const colRiscv   = Color(0xFFD2A8FF); // lavender
const colTarget  = Color(0xFF79C0FF); // light blue
const colDevice  = Color(0xFFFFA657); // amber
const colPower   = Color(0xFF56D364); // bright green

// New peripheral node accent colours
const colUsbConnector = Color(0xFF79C0FF); // light blue (same as target)
const colSwd          = Color(0xFFFF7B72); // salmon/orange-red — debug ports
const colPmod         = Color(0xFFD2A8FF); // lavender
const colMezzanine    = Color(0xFFD2A8FF); // lavender
const colButton       = Color(0xFFFFA657); // amber
const colLed          = Color(0xFFFFD700); // gold
const colFlash        = Color(0xFF8B949E); // grey
const colHyperRam     = Color(0xFF8B949E); // grey
const colUsbPhy       = Color(0xFF58A6FF); // blue

// Software / logical node accent colours (same hues, lower saturation)
const colFirmware  = Color(0xFF8B949E); // grey-blue
const colGateware  = Color(0xFF8B949E);
const colDaemon    = Color(0xFF8B949E);
const colLibrary   = Color(0xFF8B949E);

Color nodeAccent(NodeType t) => switch (t) {
      NodeType.host         => colHost,
      NodeType.apollo       => colApollo,
      NodeType.fpga         => colFpga,
      NodeType.riscv        => colRiscv,
      NodeType.targetA      => colTarget,
      NodeType.targetC      => colTarget,
      NodeType.device       => colDevice,
      NodeType.powerMonitor => colPower,
      NodeType.usbConnector => colUsbConnector,
      NodeType.swd          => colSwd,
      NodeType.pmod         => colPmod,
      NodeType.mezzanine    => colMezzanine,
      NodeType.button       => colButton,
      NodeType.led          => colLed,
      NodeType.flash        => colFlash,
      NodeType.hyperram     => colHyperRam,
      NodeType.usbPhy       => colUsbPhy,
      NodeType.firmware     => colFirmware,
      NodeType.gateware     => colGateware,
      NodeType.daemon       => colDaemon,
      NodeType.library      => colLibrary,
    };

Color statusColor(NodeStatus s) => switch (s) {
      NodeStatus.ok           => const Color(0xFF3FB950),
      NodeStatus.warning      => const Color(0xFFD29922),
      NodeStatus.error        => const Color(0xFFF85149),
      NodeStatus.disconnected => const Color(0xFF484F58),
      NodeStatus.unknown      => const Color(0xFF484F58),
    };

double statusAlpha(NodeStatus s) => switch (s) {
      NodeStatus.ok           => 1.0,
      NodeStatus.warning      => 0.90,
      NodeStatus.error        => 0.95,
      NodeStatus.disconnected => 0.45,
      NodeStatus.unknown      => 0.60,
    };

Color ttySourceColor(dynamic source) {
  // TtySource — avoid import cycle, match by runtimeType string check at call site
  return const Color(0xFF8B949E);
}

ThemeData buildTheme() => ThemeData(
      brightness: Brightness.dark,
      scaffoldBackgroundColor: bgCanvas,
      colorScheme: const ColorScheme.dark(
        surface: bgPanel,
        onSurface: textPrimary,
        primary: colHost,
      ),
      dividerColor: borderColor,
      fontFamily: 'monospace',
    );
