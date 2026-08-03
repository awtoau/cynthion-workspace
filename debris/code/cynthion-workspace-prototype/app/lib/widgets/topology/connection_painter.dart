import 'package:flutter/material.dart';
import '../../models/node.dart';
import '../../theme.dart' as theme;

class ConnectionPainter extends CustomPainter {
  final Map<String, HardwareNode> nodes;
  final List<NodeConnection> connections;

  ConnectionPainter({required this.nodes, required this.connections});

  @override
  void paint(Canvas canvas, Size size) {
    for (final conn in connections) {
      final from = nodes[conn.fromId];
      final to   = nodes[conn.toId];
      if (from == null || to == null) continue;

      final a = _center(from);
      final b = _center(to);
      final active = conn.active &&
          from.status != NodeStatus.disconnected &&
          to.status   != NodeStatus.disconnected;

      final baseColor = active
          ? theme.nodeAccent(from.type).withValues(alpha: 0.6)
          : theme.borderColor.withValues(alpha: 0.35);

      // Bezier control points: horizontal pull proportional to x-distance
      final dx = (b.dx - a.dx).abs() * 0.45;
      final path = Path()
        ..moveTo(a.dx, a.dy)
        ..cubicTo(a.dx + dx, a.dy, b.dx - dx, b.dy, b.dx, b.dy);

      final paint = Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = from.isPhysical && to.isPhysical ? 1.5 : 1.0
        ..strokeCap = StrokeCap.round
        ..color = baseColor;

      // Fault highlight
      if (from.status == NodeStatus.error || to.status == NodeStatus.error) {
        paint.color = theme.statusColor(NodeStatus.error).withValues(alpha: 0.7);
        paint.strokeWidth += 0.5;
      } else if (from.status == NodeStatus.warning || to.status == NodeStatus.warning) {
        paint.color = theme.statusColor(NodeStatus.warning).withValues(alpha: 0.7);
      }

      canvas.drawPath(path, paint);

      // Label mid-point
      if (conn.label.isNotEmpty) {
        final mid = Offset((a.dx + b.dx) / 2, (a.dy + b.dy) / 2);
        final tp = TextPainter(
          text: TextSpan(
            text: conn.label,
            style: TextStyle(
              color: theme.textMuted.withValues(alpha: active ? 0.7 : 0.3),
              fontSize: 9,
              fontFamily: 'monospace',
            ),
          ),
          textDirection: TextDirection.ltr,
        )..layout();
        tp.paint(canvas, mid - Offset(tp.width / 2, tp.height / 2));
      }
    }
  }

  Offset _center(HardwareNode n) =>
      n.position + Offset(nodeSize.width / 2, nodeSize.height / 2);

  @override
  bool shouldRepaint(ConnectionPainter old) =>
      old.nodes != nodes || old.connections != connections;
}
