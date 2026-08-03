import 'dart:convert';
import 'package:flutter/services.dart';
import 'hardware_info.dart';
import 'node.dart';

class BoardConfig {
  final String board;
  final String version;
  final List<HardwareNode> nodes;
  final List<NodeConnection> connections;

  const BoardConfig({
    required this.board,
    required this.version,
    required this.nodes,
    required this.connections,
  });

  static Future<BoardConfig> loadAsset(String assetPath) async {
    final raw = await rootBundle.loadString(assetPath);
    return BoardConfig.fromJson(jsonDecode(raw) as Map<String, dynamic>);
  }

  factory BoardConfig.fromJson(Map<String, dynamic> j) {
    final meta = j['_meta'] as Map<String, dynamic>? ?? {};
    final nodes = (j['nodes'] as List)
        .map((e) => _nodeFromJson(e as Map<String, dynamic>))
        .toList();
    final connections = (j['connections'] as List)
        .map((e) => _connFromJson(e as Map<String, dynamic>))
        .toList();
    return BoardConfig(
      board: meta['board'] as String? ?? '',
      version: meta['version'] as String? ?? '',
      nodes: nodes,
      connections: connections,
    );
  }
}

HardwareNode _nodeFromJson(Map<String, dynamic> j) {
  final pos = j['position'] as Map<String, dynamic>? ?? {'x': 0.0, 'y': 0.0};
  return HardwareNode(
    id: j['id'] as String,
    label: j['label'] as String,
    sublabel: j['sublabel'] as String? ?? '',
    type: _nodeType(j['type'] as String),
    position: Offset((pos['x'] as num).toDouble(), (pos['y'] as num).toDouble()),
    info: j['info'] != null ? _infoFromJson(j['info'] as Map<String, dynamic>) : null,
  );
}

NodeConnection _connFromJson(Map<String, dynamic> j) => NodeConnection(
      fromId: j['fromId'] as String,
      toId: j['toId'] as String,
      label: j['label'] as String? ?? '',
      active: j['active'] as bool? ?? true,
      dataActive: j['dataActive'] as bool? ?? false,
    );

HardwareInfo _infoFromJson(Map<String, dynamic> j) => HardwareInfo(
      partNumber: j['partNumber'] as String? ?? '',
      manufacturer: j['manufacturer'] as String? ?? '',
      description: j['description'] as String? ?? '',
      datasheet: j['datasheet'] as String?,
      pins: (j['pins'] as List? ?? [])
          .map((p) => _pinFromJson(p as Map<String, dynamic>))
          .toList(),
    );

NodePin _pinFromJson(Map<String, dynamic> j) => NodePin(
      j['number'] as int,
      j['name'] as String,
      j['signal'] as String,
      _pinType(j['type'] as String),
    );

// ignore: missing_return
NodeType _nodeType(String s) => switch (s) {
      'host'         => NodeType.host,
      'apollo'       => NodeType.apollo,
      'fpga'         => NodeType.fpga,
      'riscv'        => NodeType.riscv,
      'targetA'      => NodeType.targetA,
      'targetC'      => NodeType.targetC,
      'device'       => NodeType.device,
      'powerMonitor' => NodeType.powerMonitor,
      'usbConnector' => NodeType.usbConnector,
      'swd'          => NodeType.swd,
      'pmod'         => NodeType.pmod,
      'mezzanine'    => NodeType.mezzanine,
      'button'       => NodeType.button,
      'led'          => NodeType.led,
      'flash'        => NodeType.flash,
      'hyperram'     => NodeType.hyperram,
      'usbPhy'       => NodeType.usbPhy,
      'firmware'     => NodeType.firmware,
      'gateware'     => NodeType.gateware,
      'daemon'       => NodeType.daemon,
      'library'      => NodeType.library,
      _              => NodeType.device,
    };

PinType _pinType(String s) => switch (s) {
      'power'  => PinType.power,
      'gnd'    => PinType.gnd,
      'signal' => PinType.signal,
      _        => PinType.nc,
    };
