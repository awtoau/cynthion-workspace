enum DeviceVariant { stock, vendor, awtoFork, unknown }

class DeviceCapabilities {
  final DeviceVariant variant;
  final bool hasPowerMonitoring;
  final bool hasTopologyView;
  final bool hasAdvancedFeatures;
  final bool hasMultiTTY;
  final List<String> availableTTYs;
  final String? apollodVersion;
  final String? deviceSerial;

  const DeviceCapabilities({
    required this.variant,
    required this.hasPowerMonitoring,
    required this.hasTopologyView,
    required this.hasAdvancedFeatures,
    required this.hasMultiTTY,
    this.availableTTYs = const [],
    this.apollodVersion,
    this.deviceSerial,
  });

  factory DeviceCapabilities.fromJson(Map<String, dynamic> json) {
    final variant = _parseVariant(json['variant'] as String?);
    final availableTTYs = _parseTTYs(json['available_ttys']);
    final hasMultiTTY = availableTTYs.length > 1;

    return DeviceCapabilities(
      variant: variant,
      hasPowerMonitoring: json['has_power_monitoring'] as bool? ?? false,
      hasTopologyView: json['has_topology'] as bool? ?? false,
      hasAdvancedFeatures: json['has_advanced_features'] as bool? ?? false,
      hasMultiTTY: hasMultiTTY,
      availableTTYs: availableTTYs,
      apollodVersion: json['apollod_version'] as String?,
      deviceSerial: json['device_serial'] as String?,
    );
  }

  static DeviceVariant _parseVariant(String? variant) {
    return switch (variant) {
      'awto_fork' => DeviceVariant.awtoFork,
      'vendor' => DeviceVariant.vendor,
      'stock' => DeviceVariant.stock,
      _ => DeviceVariant.unknown,
    };
  }

  static List<String> _parseTTYs(dynamic ttys) {
    if (ttys is List) {
      return ttys.map((t) => t.toString()).toList();
    } else if (ttys is String) {
      return ttys.split(',').map((t) => t.trim()).toList();
    }
    return [];
  }

  static DeviceCapabilities stockDefault() => const DeviceCapabilities(
    variant: DeviceVariant.stock,
    hasPowerMonitoring: false,
    hasTopologyView: false,
    hasAdvancedFeatures: false,
    hasMultiTTY: false,
  );

  static DeviceCapabilities vendorDefault() => const DeviceCapabilities(
    variant: DeviceVariant.vendor,
    hasPowerMonitoring: false,
    hasTopologyView: false,
    hasAdvancedFeatures: false,
    hasMultiTTY: true,
    availableTTYs: ['ttyACM0', 'ttyACM1', 'ttyACM2'],
  );

  static DeviceCapabilities awtoForkDefault() => const DeviceCapabilities(
    variant: DeviceVariant.awtoFork,
    hasPowerMonitoring: true,
    hasTopologyView: true,
    hasAdvancedFeatures: true,
    hasMultiTTY: true,
    availableTTYs: ['ttyACM0', 'ttyACM1', 'ttyACM2'],
  );
}
