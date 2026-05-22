enum PinType { power, gnd, signal, nc }

class NodePin {
  final int number;
  final String name;
  final String signal; // net name from schematic
  final PinType type;

  const NodePin(this.number, this.name, this.signal, this.type);
}

class HardwareInfo {
  final String partNumber;
  final String manufacturer;
  final String description;
  final String? datasheet; // URL
  final List<NodePin> pins; // empty for non-connectors

  const HardwareInfo({
    required this.partNumber,
    this.manufacturer = '',
    required this.description,
    this.datasheet,
    this.pins = const [],
  });
}
