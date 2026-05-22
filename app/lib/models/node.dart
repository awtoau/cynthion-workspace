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

  const NodeConnection({
    required this.fromId,
    required this.toId,
    this.label = '',
    this.active = true,
    this.dataActive = false,
  });
}

const nodeSize = Size(130, 62);
const nodeCompactSize = Size(100, 48);

Size nodeSizeFor(HardwareNode n) => n.isCompact ? nodeCompactSize : nodeSize;

