import 'package:flutter/material.dart';

enum NodeType {
  // physical hardware
  host, apollo, fpga, riscv, targetA, targetC, device, powerMonitor,
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

  HardwareNode({
    required this.id,
    required this.label,
    this.sublabel = '',
    required this.type,
    this.status = NodeStatus.unknown,
    required this.position,
  });

  bool get isPhysical => switch (type) {
        NodeType.host || NodeType.apollo || NodeType.fpga ||
        NodeType.riscv || NodeType.targetA || NodeType.targetC ||
        NodeType.device || NodeType.powerMonitor => true,
        _ => false,
      };

  HardwareNode copyWith({Offset? position, NodeStatus? status}) => HardwareNode(
        id: id,
        label: label,
        sublabel: sublabel,
        type: type,
        status: status ?? this.status,
        position: position ?? this.position,
      );
}

class NodeConnection {
  final String fromId;
  final String toId;
  final String label;
  final bool active;

  const NodeConnection({
    required this.fromId,
    required this.toId,
    this.label = '',
    this.active = true,
  });
}

const nodeSize = Size(130, 62);

// Initial hardware topology — matches docs/architecture.md
// Fake/demo data for now; apollod will update status at runtime.
final defaultNodes = [
  // ── Physical hardware ────────────────────────────────────────────
  HardwareNode(id: 'host',    label: 'Host PC',    sublabel: 'linux',
      type: NodeType.host,         position: const Offset(60,  340), status: NodeStatus.ok),
  HardwareNode(id: 'apollo',  label: 'Apollo',     sublabel: 'SAMD11 · 1d50:615c · SN:005600…',
      type: NodeType.apollo,       position: const Offset(320, 220), status: NodeStatus.unknown),
  HardwareNode(id: 'pac1954', label: 'PAC1954',    sublabel: 'I²C power monitor',
      type: NodeType.powerMonitor, position: const Offset(320, 420), status: NodeStatus.unknown),
  HardwareNode(id: 'fpga',    label: 'ECP5 FPGA',  sublabel: 'LFE5U-12F',
      type: NodeType.fpga,         position: const Offset(580, 340), status: NodeStatus.unknown),
  HardwareNode(id: 'riscv',   label: 'VexRiscv',   sublabel: 'soft core',
      type: NodeType.riscv,        position: const Offset(580, 160), status: NodeStatus.unknown),
  HardwareNode(id: 'targetA', label: 'TARGET-A',   sublabel: '1d50:615b',
      type: NodeType.targetA,      position: const Offset(840, 240), status: NodeStatus.unknown),
  HardwareNode(id: 'targetC', label: 'TARGET-C',   sublabel: 'camera port',
      type: NodeType.targetC,      position: const Offset(840, 460), status: NodeStatus.unknown),
  HardwareNode(id: 'camera',  label: 'UTi261M',    sublabel: '0bda:5830 UVC',
      type: NodeType.device,       position: const Offset(1080, 460), status: NodeStatus.unknown),

  // ── Logical / software ───────────────────────────────────────────
  HardwareNode(id: 'moondancer', label: 'moondancer', sublabel: 'RISC-V firmware',
      type: NodeType.firmware,  position: const Offset(580, 60),  status: NodeStatus.unknown),
  HardwareNode(id: 'gateware',   label: 'facedancer.bit', sublabel: 'ECP5 gateware',
      type: NodeType.gateware,  position: const Offset(840, 60),  status: NodeStatus.unknown),
  HardwareNode(id: 'apollod',    label: 'apollod',    sublabel: 'TTY daemon',
      type: NodeType.daemon,    position: const Offset(60,  160),  status: NodeStatus.unknown),
  HardwareNode(id: 'facedancer', label: 'facedancer',  sublabel: 'Python host lib',
      type: NodeType.library,   position: const Offset(60,  520),  status: NodeStatus.unknown),
];

const defaultConnections = [
  NodeConnection(fromId: 'host',       toId: 'apollo',      label: 'CONTROL USB'),
  NodeConnection(fromId: 'apollo',     toId: 'fpga',        label: 'JTAG + UART'),
  NodeConnection(fromId: 'apollo',     toId: 'pac1954',     label: 'I²C'),
  NodeConnection(fromId: 'fpga',       toId: 'riscv',       label: 'internal'),
  NodeConnection(fromId: 'fpga',       toId: 'targetA',     label: 'PHY A'),
  NodeConnection(fromId: 'fpga',       toId: 'targetC',     label: 'PHY C'),
  NodeConnection(fromId: 'targetC',    toId: 'camera',      label: 'UVC'),
  NodeConnection(fromId: 'moondancer', toId: 'riscv',       label: 'runs on'),
  NodeConnection(fromId: 'gateware',   toId: 'fpga',        label: 'loaded into'),
  NodeConnection(fromId: 'apollod',    toId: 'apollo',      label: 'ttyACM0-2'),
  NodeConnection(fromId: 'facedancer', toId: 'targetA',     label: 'GCP API'),
];
