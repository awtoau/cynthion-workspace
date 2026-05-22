import 'package:flutter/material.dart';
import '../../models/node.dart';
import '../../theme.dart' as theme;

class HardwareNodeWidget extends StatelessWidget {
  final HardwareNode node;
  final bool selected;
  final bool hovered;
  final VoidCallback? onTap;
  final ValueChanged<String?>? onHoverChanged; // passes node.id or null

  const HardwareNodeWidget({
    super.key,
    required this.node,
    this.selected = false,
    this.hovered = false,
    this.onTap,
    this.onHoverChanged,
  });

  @override
  Widget build(BuildContext context) {
    final accent = theme.nodeAccent(node.type);
    final sColor = theme.statusColor(node.status);
    final alpha = theme.statusAlpha(node.status);
    final isLogical = !node.isPhysical;
    final isCompact = node.isCompact;
    final sz = nodeSizeFor(node);
    final hasPins = node.info?.pins.isNotEmpty ?? false;

    // Border brightens on hover/select
    final borderAlpha = selected
        ? 1.0
        : hovered
            ? 0.75
            : isLogical
                ? 0.5
                : 0.4;

    return MouseRegion(
      onEnter: (_) => onHoverChanged?.call(node.id),
      onExit: (_) => onHoverChanged?.call(null),
      child: GestureDetector(
        onTap: onTap,
        child: Opacity(
          opacity: alpha,
          child: Container(
            width: sz.width,
            height: sz.height,
            decoration: BoxDecoration(
              color: theme.bgCard,
              border: Border.all(
                color: selected || hovered
                    ? accent
                    : isLogical
                        ? theme.borderColor.withValues(alpha: borderAlpha)
                        : accent.withValues(alpha: borderAlpha),
                width: selected ? 1.5 : (hovered ? 1.2 : 1.0),
              ),
              borderRadius: BorderRadius.circular(isLogical ? 8 : 6),
              boxShadow: isLogical
                  ? null
                  : [
                      BoxShadow(
                        color: accent.withValues(alpha: hovered ? 0.22 : 0.12),
                        blurRadius: hovered ? 12 : 8,
                        spreadRadius: 1,
                      ),
                    ],
            ),
            child: Padding(
              padding: EdgeInsets.symmetric(
                horizontal: isCompact ? 6 : 8,
                vertical: isCompact ? 4 : 6,
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Row(children: [
                    _icon(accent, isLogical, isCompact),
                    SizedBox(width: isCompact ? 4 : 5),
                    Expanded(
                      child: Text(
                        node.label,
                        style: TextStyle(
                          color: isLogical ? theme.textMuted : theme.textPrimary,
                          fontSize: isCompact ? 10 : 11,
                          fontWeight: FontWeight.w600,
                          letterSpacing: 0.2,
                        ),
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                    if (hasPins)
                      Padding(
                        padding: const EdgeInsets.only(left: 2),
                        child: Icon(
                          Icons.info_outline,
                          size: 10,
                          color: accent.withValues(alpha: 0.6),
                        ),
                      ),
                    const SizedBox(width: 2),
                    _statusDot(sColor),
                  ]),
                  SizedBox(height: isCompact ? 1 : 2),
                  Text(
                    node.sublabel,
                    style: TextStyle(
                      color: theme.textMuted.withValues(alpha: 0.7),
                      fontSize: isCompact ? 8 : 9,
                      letterSpacing: 0.1,
                    ),
                    overflow: TextOverflow.ellipsis,
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  Widget _icon(Color accent, bool logical, bool compact) {
    final icon = switch (node.type) {
      NodeType.host         => Icons.computer,
      NodeType.apollo       => Icons.memory,
      NodeType.fpga         => Icons.developer_board,
      NodeType.riscv        => Icons.precision_manufacturing,
      NodeType.targetA      => Icons.usb,
      NodeType.targetC      => Icons.usb,
      NodeType.device       => Icons.camera_alt_outlined,
      NodeType.powerMonitor => Icons.bolt,
      NodeType.usbConnector => Icons.usb,
      NodeType.swd          => Icons.cable,
      NodeType.pmod         => Icons.input,
      NodeType.mezzanine    => Icons.view_column_outlined,
      NodeType.button       => Icons.radio_button_checked,
      NodeType.led          => Icons.lightbulb_outline,
      NodeType.flash        => Icons.storage,
      NodeType.hyperram     => Icons.memory_outlined,
      NodeType.usbPhy       => Icons.settings_input_component,
      NodeType.firmware     => Icons.code,
      NodeType.gateware     => Icons.layers,
      NodeType.daemon       => Icons.terminal,
      NodeType.library      => Icons.library_books_outlined,
    };
    return Icon(icon,
        size: compact ? 11 : 13, color: logical ? theme.textMuted : accent);
  }

  Widget _statusDot(Color color) => Container(
        width: 7,
        height: 7,
        decoration: BoxDecoration(
          color: color,
          shape: BoxShape.circle,
          boxShadow: [
            BoxShadow(color: color.withValues(alpha: 0.5), blurRadius: 4)
          ],
        ),
      );
}
