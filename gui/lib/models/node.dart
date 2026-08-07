import 'package:flutter/material.dart';
import 'hardware_info.dart';

enum NodeType {
  // physical hardware
  host, apollo, fpga, riscv, targetA, targetC, device, powerMonitor,
  // connectors / peripherals
  usbConnector, swd, pmod, mezzanine, button, led, flash, hyperram, usbPhy,
  // logical / software
  firmware, gateware, daemon, library,
}

enum NodeStatus { unknown, ok, warning, error, disconnected }

class HardwareNode {
  final String id;
  final String label;
  final String sublabel;
  final NodeType type;
  NodeStatus status;
  Offset position; // canvas-space
  final HardwareInfo? info;

  HardwareNode({
    required this.id,
    required this.label,
    this.sublabel = '',
    required this.type,
    this.status = NodeStatus.unknown,
    required this.position,
    this.info,
  });

  bool get isPhysical => switch (type) {
        NodeType.host ||
        NodeType.apollo ||
        NodeType.fpga ||
        NodeType.riscv ||
        NodeType.targetA ||
        NodeType.targetC ||
        NodeType.device ||
        NodeType.powerMonitor ||
        NodeType.usbConnector ||
        NodeType.swd ||
        NodeType.pmod ||
        NodeType.mezzanine ||
        NodeType.button ||
        NodeType.led ||
        NodeType.flash ||
        NodeType.hyperram ||
        NodeType.usbPhy =>
          true,
        _ => false,
      };

  bool get isCompact => switch (type) {
        NodeType.usbConnector ||
        NodeType.swd ||
        NodeType.pmod ||
        NodeType.mezzanine ||
        NodeType.button ||
        NodeType.led =>
          true,
        _ => false,
      };

  HardwareNode copyWith({Offset? position, NodeStatus? status}) => HardwareNode(
        id: id,
        label: label,
        sublabel: sublabel,
        type: type,
        status: status ?? this.status,
        position: position ?? this.position,
        info: info,
      );
}

class NodeConnection {
  final String fromId;
  final String toId;
  final String label;
  final bool active;
  final bool dataActive;

  // ── Interface description ──────────────────────────────────────────────────
  // Populated by gui/tools/extract-hardware.py from the KiCad netlist. A board
  // file written by hand leaves these empty and the GUI falls back to [label].
  /// `UART`, `SPI`, `JTAG`, `USB2`, `HyperBus`, `SWD`, `I2C`, `GPIO`, `power`…
  final String interface;

  /// Net names crossing between the two components, e.g. `MCU_UART0_RX`.
  final List<String> nets;

  /// Power domain the signals sit in, e.g. `3.3V`.
  final String voltage;

  /// One-liner for the hover chip, e.g. `UART 3.3 V CMOS`.
  final String signalType;

  /// `MCU→FPGA` where the schematic says which end drives.
  final String direction;

  const NodeConnection({
    required this.fromId,
    required this.toId,
    this.label = '',
    this.active = true,
    this.dataActive = false,
    this.interface = '',
    this.nets = const [],
    this.voltage = '',
    this.signalType = '',
    this.direction = '',
  });

  /// What the hover chip shows: everything the extractor knew, or the plain
  /// label when it knew nothing.
  String get hoverLabel {
    final parts = <String>[];
    if (direction.isNotEmpty) {
      parts.add(direction);
    } else if (label.isNotEmpty) {
      parts.add(label);
    }
    if (signalType.isNotEmpty) {
      parts.add(signalType);
    } else if (interface.isNotEmpty) {
      parts.add(voltage.isEmpty ? interface : '$interface $voltage');
    }
    if (nets.isNotEmpty) {
      // A HyperBus edge carries 13 nets and the mezzanine 22; the whole list
      // makes a chip wider than the canvas.
      const shown = 6;
      final head = nets.take(shown).join(', ');
      parts.add(nets.length > shown
          ? '[$head, +${nets.length - shown} more]'
          : '[$head]');
    }
    return parts.join('  ');
  }
}

const nodeSize = Size(130, 62);
const nodeCompactSize = Size(100, 48);

Size nodeSizeFor(HardwareNode n) => n.isCompact ? nodeCompactSize : nodeSize;

