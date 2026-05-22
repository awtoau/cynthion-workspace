import 'package:flutter/material.dart';
import '../../models/hardware_info.dart';
import '../../models/node.dart';
import '../../theme.dart' as theme;

class PinoutDialog extends StatelessWidget {
  final HardwareNode node;

  const PinoutDialog({super.key, required this.node});

  @override
  Widget build(BuildContext context) {
    final info = node.info!;
    final accent = theme.nodeAccent(node.type);

    return Dialog(
      backgroundColor: theme.bgPanel,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(10),
        side: BorderSide(color: accent.withValues(alpha: 0.4)),
      ),
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 480, maxHeight: 600),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            // Header
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
              decoration: BoxDecoration(
                color: theme.bgCard,
                borderRadius: const BorderRadius.vertical(top: Radius.circular(10)),
                border: Border(
                  bottom: BorderSide(color: theme.borderColor),
                ),
              ),
              child: Row(
                children: [
                  Icon(Icons.cable, size: 16, color: accent),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          node.label,
                          style: const TextStyle(
                            color: theme.textPrimary,
                            fontSize: 13,
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                        if (info.partNumber.isNotEmpty)
                          Text(
                            info.partNumber,
                            style: TextStyle(
                              color: accent,
                              fontSize: 10,
                              fontFamily: 'monospace',
                            ),
                          ),
                      ],
                    ),
                  ),
                  IconButton(
                    icon: const Icon(Icons.close, size: 16),
                    color: theme.textMuted,
                    onPressed: () => Navigator.of(context).pop(),
                    padding: EdgeInsets.zero,
                    constraints: const BoxConstraints(minWidth: 24, minHeight: 24),
                  ),
                ],
              ),
            ),

            // Pin table
            Flexible(
              child: SingleChildScrollView(
                padding: const EdgeInsets.all(12),
                child: Table(
                  columnWidths: const {
                    0: FixedColumnWidth(36),
                    1: FixedColumnWidth(72),
                    2: FlexColumnWidth(),
                    3: FixedColumnWidth(56),
                  },
                  border: TableBorder(
                    horizontalInside: BorderSide(
                      color: theme.borderColor.withValues(alpha: 0.5),
                      width: 0.5,
                    ),
                  ),
                  children: [
                    // Header row
                    TableRow(
                      decoration: BoxDecoration(color: theme.bgCard),
                      children: [
                        _headerCell('#'),
                        _headerCell('Name'),
                        _headerCell('Signal'),
                        _headerCell('Type'),
                      ],
                    ),
                    // Pin rows
                    ...info.pins.map((pin) => TableRow(
                          children: [
                            _dataCell(pin.number.toString(),
                                color: theme.textMuted),
                            _dataCell(pin.name),
                            _dataCell(
                              pin.signal,
                              mono: true,
                              color: theme.textMuted,
                            ),
                            _pinTypeCell(pin.type),
                          ],
                        )),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _headerCell(String text) => Padding(
        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 6),
        child: Text(
          text,
          style: const TextStyle(
            color: theme.textMuted,
            fontSize: 9,
            fontWeight: FontWeight.w600,
            letterSpacing: 0.5,
          ),
        ),
      );

  Widget _dataCell(String text, {Color? color, bool mono = false}) => Padding(
        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 5),
        child: Text(
          text,
          style: TextStyle(
            color: color ?? theme.textPrimary,
            fontSize: 10,
            fontFamily: mono ? 'monospace' : null,
          ),
          overflow: TextOverflow.ellipsis,
        ),
      );

  Widget _pinTypeCell(PinType type) {
    final (label, color) = switch (type) {
      PinType.power  => ('PWR', const Color(0xFFF85149)),
      PinType.gnd    => ('GND', theme.textMuted),
      PinType.signal => ('SIG', const Color(0xFF3FB950)),
      PinType.nc     => ('NC', theme.borderColor),
    };
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 4),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 2),
        decoration: BoxDecoration(
          color: color.withValues(alpha: 0.15),
          borderRadius: BorderRadius.circular(3),
          border: Border.all(color: color.withValues(alpha: 0.4), width: 0.5),
        ),
        child: Text(
          label,
          style: TextStyle(
            color: color,
            fontSize: 8,
            fontWeight: FontWeight.w600,
            letterSpacing: 0.3,
          ),
          textAlign: TextAlign.center,
        ),
      ),
    );
  }
}
