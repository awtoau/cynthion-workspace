import 'package:flutter/material.dart';
import '../../models/node.dart';
import '../../theme.dart' as theme;

class HardwareNodeWidget extends StatelessWidget {
  final HardwareNode node;
  final bool selected;
  final VoidCallback? onTap;

  const HardwareNodeWidget({
    super.key,
    required this.node,
    this.selected = false,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final accent  = theme.nodeAccent(node.type);
    final sColor  = theme.statusColor(node.status);
    final alpha   = theme.statusAlpha(node.status);
    final isLogical = !node.isPhysical;

    return GestureDetector(
      onTap: onTap,
      child: Opacity(
        opacity: alpha,
        child: Container(
          width: nodeSize.width,
          height: nodeSize.height,
          decoration: BoxDecoration(
            color: theme.bgCard,
            border: Border.all(
              color: selected
                  ? accent
                  : isLogical
                      ? theme.borderColor.withValues(alpha: 0.5)
                      : accent.withValues(alpha: 0.4),
              width: selected ? 1.5 : 1.0,
            ),
            borderRadius: BorderRadius.circular(isLogical ? 8 : 6),
            // Dashed border simulation for logical nodes via box-shadow
            boxShadow: isLogical
                ? null
                : [
                    BoxShadow(
                      color: accent.withValues(alpha: 0.12),
                      blurRadius: 8,
                      spreadRadius: 1,
                    )
                  ],
          ),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Row(children: [
                  _icon(accent, isLogical),
                  const SizedBox(width: 5),
                  Expanded(
                    child: Text(
                      node.label,
                      style: TextStyle(
                        color: isLogical
                            ? theme.textMuted
                            : theme.textPrimary,
                        fontSize: 11,
                        fontWeight: FontWeight.w600,
                        letterSpacing: 0.2,
                      ),
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                  _statusDot(sColor),
                ]),
                const SizedBox(height: 2),
                Text(
                  node.sublabel,
                  style: TextStyle(
                    color: theme.textMuted.withValues(alpha: 0.7),
                    fontSize: 9,
                    letterSpacing: 0.1,
                  ),
                  overflow: TextOverflow.ellipsis,
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _icon(Color accent, bool logical) {
    final icon = switch (node.type) {
      NodeType.host         => Icons.computer,
      NodeType.apollo       => Icons.memory,
      NodeType.fpga         => Icons.developer_board,
      NodeType.riscv        => Icons.precision_manufacturing,
      NodeType.targetA      => Icons.usb,
      NodeType.targetC      => Icons.usb,
      NodeType.device       => Icons.camera_alt_outlined,
      NodeType.powerMonitor => Icons.bolt,
      NodeType.firmware     => Icons.code,
      NodeType.gateware     => Icons.layers,
      NodeType.daemon       => Icons.terminal,
      NodeType.library      => Icons.library_books_outlined,
    };
    return Icon(icon,
        size: 13, color: logical ? theme.textMuted : accent);
  }

  Widget _statusDot(Color color) => Container(
        width: 7,
        height: 7,
        decoration: BoxDecoration(
          color: color,
          shape: BoxShape.circle,
          boxShadow: [BoxShadow(color: color.withValues(alpha: 0.5), blurRadius: 4)],
        ),
      );
}
