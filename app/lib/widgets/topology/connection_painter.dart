import 'dart:io';
import 'dart:math';
import 'package:flutter/material.dart';
import '../../models/node.dart';
import '../../theme.dart' as theme;

class ConnectionPainter extends CustomPainter {
  final Map<String, HardwareNode> nodes;
  final List<NodeConnection> connections;
  final double animPhase; // 0.0–1.0 for traveling-dash animation
  final Offset? hoverPoint; // canvas-space hover position
  final Matrix4 transform; // InteractiveViewer transform applied in paint()
  final Map<String, Offset> dragOffsets; // Local offsets during node drag

  ConnectionPainter({
    required this.nodes,
    required this.connections,
    this.animPhase = 0.0,
    this.hoverPoint,
    required this.transform,
    this.dragOffsets = const {},
  });

  // ── Debug logging ──────────────────────────────────────────────────────────
  static int _paintCalls = 0;
  static Offset? _lastLoggedHover;
  static bool _logInited = false;

  static void _log(String msg) {
    try {
      final f = File('/home/dan/git/cynthion-workspace/app/tmp/connection_debug.log');
      if (!_logInited) {
        f.writeAsStringSync('=== ConnectionPainter debug ${DateTime.now()} ===\n');
        _logInited = true;
      }
      f.writeAsStringSync(
        '${DateTime.now().toIso8601String().substring(11, 23)} $msg\n',
        mode: FileMode.append,
      );
    } catch (_) {}
  }

  // ── Edge-attachment helper ──────────────────────────────────────────────────
  /// Returns the point on the closest edge of [n]'s rectangle.
  /// Exits perpendicular to that edge with padding to avoid overlapping.
  Offset _edgePoint(HardwareNode n, Offset toward) {
    final sz = nodeSizeFor(n);
    final dragOffset = dragOffsets[n.id] ?? Offset.zero;
    final nLeft = n.position.dx + dragOffset.dx;
    final nTop = n.position.dy + dragOffset.dy;
    final nRight = nLeft + sz.width;
    final nBottom = nTop + sz.height;
    final nCenterX = nLeft + sz.width / 2;
    final nCenterY = nTop + sz.height / 2;

    // Find closest edge: left, right, top, or bottom
    final dx = toward.dx - nCenterX;
    final dy = toward.dy - nCenterY;
    final absX = dx.abs();
    final absY = dy.abs();

    // Padding: move exit point away from node to prevent curve overlap
    const padding = 6.0;

    Offset result;

    // Decide which edge is closest based on angle
    if (absX > absY) {
      // Left or right edge — exit perpendicular (horizontally)
      if (dx > 0) {
        // Right edge
        result = Offset(nRight + padding, nCenterY);
      } else {
        // Left edge
        result = Offset(nLeft - padding, nCenterY);
      }
    } else {
      // Top or bottom edge — exit perpendicular (vertically)
      if (dy > 0) {
        // Bottom edge
        result = Offset(nCenterX, nBottom + padding);
      } else {
        // Top edge
        result = Offset(nCenterX, nTop - padding);
      }
    }

    _log('edge ${n.id} node=${nLeft.toStringAsFixed(0)},${nTop.toStringAsFixed(0)} toward=${toward.dx.toStringAsFixed(0)},${toward.dy.toStringAsFixed(0)} → result=${result.dx.toStringAsFixed(1)},${result.dy.toStringAsFixed(1)}');
    return result;
  }

  /// Exits vertically from the node: top or bottom edge.
  /// [goingDown] indicates direction: true = bottom, false = top.
  Offset _edgePointVertical(HardwareNode n, bool goingDown) {
    final sz = nodeSizeFor(n);
    final dragOffset = dragOffsets[n.id] ?? Offset.zero;
    final nLeft = n.position.dx + dragOffset.dx;
    final nTop = n.position.dy + dragOffset.dy;
    final nRight = nLeft + sz.width;
    final nBottom = nTop + sz.height;
    final nCenterX = nLeft + sz.width / 2;
    const padding = 6.0;

    return Offset(
      nCenterX,
      goingDown ? nBottom + padding : nTop - padding,
    );
  }

  /// Exits horizontally from the node: left or right edge.
  /// [goingRight] indicates direction: true = right, false = left.
  Offset _edgePointHorizontal(HardwareNode n, bool goingRight) {
    final sz = nodeSizeFor(n);
    final dragOffset = dragOffsets[n.id] ?? Offset.zero;
    final nLeft = n.position.dx + dragOffset.dx;
    final nTop = n.position.dy + dragOffset.dy;
    final nRight = nLeft + sz.width;
    final nCenterY = nTop + sz.height / 2;
    const padding = 6.0;

    return Offset(
      goingRight ? nRight + padding : nLeft - padding,
      nCenterY,
    );
  }

  Offset _center(HardwareNode n) {
    final sz = nodeSizeFor(n);
    final dragOffset = dragOffsets[n.id] ?? Offset.zero;
    return n.position + dragOffset + Offset(sz.width / 2, sz.height / 2);
  }

  // ── Bezier path with perpendicular exits ──────────────────────────────────
  Path _buildPath(Offset a, Offset b, {String connLabel = ''}) {
    // Bezier curve that exits perpendicular to edges

    final dx = (b.dx - a.dx);
    final dy = (b.dy - a.dy);
    final distance = sqrt(dx * dx + dy * dy);

    // Control point distance: proportional to path length
    final ctrlDist = (distance * 0.4).clamp(40.0, 250.0);

    final absX = dx.abs();
    final absY = dy.abs();

    Offset cp1, cp2;

    // Position control points perpendicular to edges for 90-degree exits
    if (absX > absY) {
      // Horizontal path: control points go perpendicular (vertically)
      // This makes curve exit horizontally, then curve vertically, then exit horizontally
      final sign = dy > 0 ? 1.0 : -1.0;
      cp1 = Offset(a.dx + ctrlDist * 0.5, a.dy + ctrlDist * sign);
      cp2 = Offset(b.dx - ctrlDist * 0.5, b.dy - ctrlDist * sign);
    } else {
      // Vertical path: control points go perpendicular (horizontally)
      final sign = dx > 0 ? 1.0 : -1.0;
      cp1 = Offset(a.dx + ctrlDist * sign, a.dy + ctrlDist * 0.5);
      cp2 = Offset(b.dx - ctrlDist * sign, b.dy - ctrlDist * 0.5);
    }

    if (connLabel == 'targetC→camera' || connLabel == 'facedancer→targetA' || connLabel == 'fpga→usb_phy_a') {
      _log('path $connLabel: a=${a.dx.toStringAsFixed(1)},${a.dy.toStringAsFixed(1)} b=${b.dx.toStringAsFixed(1)},${b.dy.toStringAsFixed(1)} cp1=${cp1.dx.toStringAsFixed(1)},${cp1.dy.toStringAsFixed(1)} cp2=${cp2.dx.toStringAsFixed(1)},${cp2.dy.toStringAsFixed(1)}');
    }

    return Path()
      ..moveTo(a.dx, a.dy)
      ..cubicTo(cp1.dx, cp1.dy, cp2.dx, cp2.dy, b.dx, b.dy);
  }

  // ── Animated dashes ────────────────────────────────────────────────────────
  final Paint _dashPaint = Paint()
    ..style = PaintingStyle.stroke
    ..strokeWidth = 2.0
    ..strokeCap = StrokeCap.round;

  void _drawDashes(Canvas canvas, Path path, Color color, double phase) {
    // PathMetrics is a one-shot iterable — use for-in, not isEmpty+first.
    for (final metric in path.computeMetrics()) {
      final total = metric.length;
      const dashLen = 10.0;
      const gap = 8.0;
      const period = dashLen + gap;
      final offset = phase * period;
      var pos = -(offset % period);
      while (pos < total) {
        final s = pos.clamp(0.0, total);
        final e = (pos + dashLen).clamp(0.0, total);
        if (e > s) {
          canvas.drawPath(
            metric.extractPath(s, e),
            _dashPaint..color = color.withValues(alpha: 0.9),
          );
        }
        pos += period;
      }
      break; // cubic bezier has exactly one contour
    }
  }

  // ── Hover proximity check ─────────────────────────────────────────────────
  /// Returns true if [pt] is within [threshold] pixels of any point on [path].
  bool _isNearPath(Path path, Offset pt, double threshold) {
    // PathMetrics is a one-shot iterable — use for-in, not isEmpty+first.
    for (final metric in path.computeMetrics()) {
      final total = metric.length;
      const step = 4.0;
      for (var t = 0.0; t <= total; t += step) {
        final tang = metric.getTangentForOffset(t);
        if (tang == null) continue;
        if ((tang.position - pt).distance < threshold) return true;
      }
    }
    return false;
  }

  // ── Label chip ─────────────────────────────────────────────────────────────
  void _drawLabelChip(Canvas canvas, String label, Offset pos) {
    if (label.isEmpty) return;
    final tp = TextPainter(
      text: TextSpan(
        text: label,
        style: const TextStyle(
          color: theme.textPrimary,
          fontSize: 9,
          fontFamily: 'monospace',
        ),
      ),
      textDirection: TextDirection.ltr,
    )..layout();

    const pad = 4.0;
    final rect = Rect.fromLTWH(
      pos.dx - tp.width / 2 - pad,
      pos.dy - tp.height / 2 - pad,
      tp.width + pad * 2,
      tp.height + pad * 2,
    );
    canvas.drawRRect(
      RRect.fromRectAndRadius(rect, const Radius.circular(4)),
      Paint()..color = theme.bgCard,
    );
    canvas.drawRRect(
      RRect.fromRectAndRadius(rect, const Radius.circular(4)),
      Paint()
        ..style = PaintingStyle.stroke
        ..color = theme.borderColor,
    );
    tp.paint(canvas, pos - Offset(tp.width / 2, tp.height / 2));
  }

  @override
  void paint(Canvas canvas, Size size) {
    // Apply InteractiveViewer transform so canvas-space coords map to viewport.
    // ConnectionPainter lives outside InteractiveViewer, so we do this manually.
    canvas.save();
    canvas.transform(transform.storage);

    // ── Debug logging (throttled) ─────────────────────────────────────────
    _paintCalls++;
    final hoverChanged = hoverPoint != _lastLoggedHover;
    if (_paintCalls <= 3 || hoverChanged || _paintCalls % 120 == 0) {
      final sc = transform.storage[0]; // uniform scale
      final tx = transform.storage[12];
      final ty = transform.storage[13];
      _log('paint#$_paintCalls nodes=${nodes.length} conns=${connections.length}'
          ' hover=$hoverPoint scale=${sc.toStringAsFixed(2)}'
          ' pan=(${tx.toStringAsFixed(1)},${ty.toStringAsFixed(1)})');
      _lastLoggedHover = hoverPoint;
    }

    // First pass: find which connection (if any) is hovered
    String? hoveredConnKey;
    Offset? hoveredMidpoint;
    if (hoverPoint != null) {
      for (final conn in connections) {
        final from = nodes[conn.fromId];
        final to = nodes[conn.toId];
        if (from == null || to == null) continue;
        final a = _edgePoint(from, _center(to));
        final b = _edgePoint(to, _center(from));
        final path = _buildPath(a, b);
        if (_isNearPath(path, hoverPoint!, 8.0)) {
          hoveredConnKey = '${conn.fromId}→${conn.toId}';
          // Midpoint along the path for label placement
          for (final m in path.computeMetrics()) {
            final tang = m.getTangentForOffset(m.length / 2);
            hoveredMidpoint = tang?.position ?? hoverPoint;
            break;
          }
          break;
        }
      }
    }

    for (final conn in connections) {
      final from = nodes[conn.fromId];
      final to = nodes[conn.toId];
      if (from == null || to == null) continue;

      final centerA = _center(from);
      final centerB = _center(to);

      // Get edge points based on closest edge to target
      var a = _edgePoint(from, centerB);
      var b = _edgePoint(to, centerA);

      final connLabel = '${conn.fromId}→${conn.toId}';
      final dx = b.dx - a.dx;
      final dy = b.dy - a.dy;

      _log('conn $connLabel a=${a.dx.toStringAsFixed(1)},${a.dy.toStringAsFixed(1)} b=${b.dx.toStringAsFixed(1)},${b.dy.toStringAsFixed(1)} dx=${dx.toStringAsFixed(1)} dy=${dy.toStringAsFixed(1)}');

      final path = _buildPath(a, b, connLabel: connLabel);

      final isHovered = hoveredConnKey == '${conn.fromId}→${conn.toId}';

      final active = conn.active &&
          from.status != NodeStatus.disconnected &&
          to.status != NodeStatus.disconnected;

      Color baseColor = active
          ? theme.nodeAccent(from.type).withValues(alpha: isHovered ? 1.0 : 0.8)
          : theme.borderColor.withValues(alpha: 0.5);

      double strokeWidth =
          from.isPhysical && to.isPhysical ? 2.0 : 1.5;

      if (isHovered) strokeWidth += 0.5;

      // Fault highlight
      if (from.status == NodeStatus.error || to.status == NodeStatus.error) {
        baseColor =
            theme.statusColor(NodeStatus.error).withValues(alpha: 0.7);
        strokeWidth += 0.5;
      } else if (from.status == NodeStatus.warning ||
          to.status == NodeStatus.warning) {
        baseColor =
            theme.statusColor(NodeStatus.warning).withValues(alpha: 0.7);
      }

      final paint = Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = strokeWidth
        ..strokeCap = StrokeCap.round
        ..color = baseColor;

      canvas.drawPath(path, paint);

      // Animated dashes on active data connections
      if (conn.dataActive && active) {
        _drawDashes(canvas, path, baseColor, animPhase);
      }

      // Hover label chip
      if (isHovered && conn.label.isNotEmpty && hoveredMidpoint != null) {
        _drawLabelChip(canvas, conn.label, hoveredMidpoint);
      } else if (!isHovered && conn.label.isNotEmpty) {
        // Faint static label at midpoint
        final mid = Offset((a.dx + b.dx) / 2, (a.dy + b.dy) / 2);
        final tp = TextPainter(
          text: TextSpan(
            text: conn.label,
            style: TextStyle(
              color: theme.textMuted.withValues(alpha: active ? 0.5 : 0.25),
              fontSize: 9,
              fontFamily: 'monospace',
            ),
          ),
          textDirection: TextDirection.ltr,
        )..layout();
        tp.paint(canvas, mid - Offset(tp.width / 2, tp.height / 2));
      }
    }

    canvas.restore();
  }

  @override
  bool shouldRepaint(ConnectionPainter old) =>
      old.dragOffsets != dragOffsets ||
      old.animPhase != animPhase ||
      old.hoverPoint != hoverPoint;
}
