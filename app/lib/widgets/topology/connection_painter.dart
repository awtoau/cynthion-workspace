import 'dart:io';
import 'package:flutter/material.dart';
import '../../models/node.dart';
import '../../theme.dart' as theme;

class ConnectionPainter extends CustomPainter {
  final Map<String, HardwareNode> nodes;
  final List<NodeConnection> connections;
  final double animPhase; // 0.0–1.0 for traveling-dash animation
  final Offset? hoverPoint; // canvas-space hover position
  final Matrix4 transform; // InteractiveViewer transform applied in paint()

  ConnectionPainter({
    required this.nodes,
    required this.connections,
    this.animPhase = 0.0,
    this.hoverPoint,
    required this.transform,
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
  /// Returns the point on the boundary of [n]'s rectangle where the line
  /// from n's centre toward [toward] exits the rectangle.
  Offset _edgePoint(HardwareNode n, Offset toward) {
    final sz = nodeSizeFor(n);
    final cx = n.position.dx + sz.width / 2;
    final cy = n.position.dy + sz.height / 2;
    final hw = sz.width / 2;
    final hh = sz.height / 2;

    final dx = toward.dx - cx;
    final dy = toward.dy - cy;

    if (dx == 0 && dy == 0) return Offset(cx, cy);

    final tx = dx != 0 ? hw / dx.abs() : double.infinity;
    final ty = dy != 0 ? hh / dy.abs() : double.infinity;
    final t = tx < ty ? tx : ty;

    return Offset(cx + dx * t, cy + dy * t);
  }

  Offset _center(HardwareNode n) {
    final sz = nodeSizeFor(n);
    return n.position + Offset(sz.width / 2, sz.height / 2);
  }

  // ── Bezier builder ─────────────────────────────────────────────────────────
  Path _buildPath(Offset a, Offset b) {
    final dx = (b.dx - a.dx).abs() * 0.45;
    return Path()
      ..moveTo(a.dx, a.dy)
      ..cubicTo(a.dx + dx, a.dy, b.dx - dx, b.dy, b.dx, b.dy);
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
      final a = _edgePoint(from, centerB);
      final b = _edgePoint(to, centerA);
      final path = _buildPath(a, b);

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
  bool shouldRepaint(ConnectionPainter old) => true;
}
