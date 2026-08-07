enum PinType { power, gnd, signal, nc }

class NodePin {
  /// Pin designator as the schematic writes it. A string, not an int: a USB-C
  /// receptacle numbers its pins `A1`…`B12` and a BGA uses `T13`. Casting this
  /// to int threw on the first connector the extractor filled in, and the whole
  /// board file failed to parse — the topology came up empty with no error.
  final String number;
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
