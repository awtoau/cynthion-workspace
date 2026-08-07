import 'dart:math';
import 'package:flutter/material.dart';
import '../../models/node.dart';
import '../../theme.dart' as theme;

/// One routed connection: the path to stroke, and the point on it where a
/// label sits.  Built once per connection per paint and thrown away.
class _Route {
  final Path path;

  /// Half-way along the wire, nudged clear of it so the text is not struck
  /// through by the line it names: above a horizontal run, right of a vertical.
  final Offset labelPoint;

  _Route(this.path, Offset mid, bool horizontalRun)
      : labelPoint =
            mid + (horizontalRun ? const Offset(0, -9) : const Offset(9, 0));
}

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

  /// Gap between a node's edge and the start of its wire.
  static const double _pad = 6.0;

  /// Radius of the rounded turn.  Clamped per corner to half the shorter leg,
  /// so short wires round off rather than overshooting into the node.
  static const double _corner = 14.0;

  /// Below this, two faces count as aligned and the wire runs straight.
  static const double _alignEps = 1.0;

  // ── Geometry ───────────────────────────────────────────────────────────────
  /// The node's rectangle in canvas space, grown by [_pad] so wires stop clear
  /// of the box instead of touching it.
  Rect _rectOf(HardwareNode n) {
    final sz = nodeSizeFor(n);
    final origin = n.position + (dragOffsets[n.id] ?? Offset.zero);
    return Rect.fromLTWH(origin.dx, origin.dy, sz.width, sz.height)
        .inflate(_pad);
  }

  /// Appends a rounded 90° turn at [corner] to [path], coming from [from] and
  /// leaving toward [to].  A conic of weight cos(45°) is exactly a quarter
  /// circle, so the fillet is a true arc rather than an approximation.
  void _turn(Path path, Offset from, Offset corner, Offset to) {
    final inLen = (corner - from).distance;
    final outLen = (to - corner).distance;
    final r = min(_corner, min(inLen, outLen) / 2);
    if (r < 0.5) {
      path.lineTo(corner.dx, corner.dy);
      path.lineTo(to.dx, to.dy);
      return;
    }
    final a = corner + (from - corner) / inLen * r;
    final b = corner + (to - corner) / outLen * r;
    path.lineTo(a.dx, a.dy);
    path.conicTo(corner.dx, corner.dy, b.dx, b.dy, sqrt2 / 2);
    path.lineTo(to.dx, to.dy);
  }

  Path _straight(Offset a, Offset b) => Path()
    ..moveTo(a.dx, a.dy)
    ..lineTo(b.dx, b.dy);

  Path _elbow(Offset a, Offset corner, Offset b) {
    final path = Path()..moveTo(a.dx, a.dy);
    _turn(path, a, corner, b);
    return path;
  }

  /// Routes [a] → [b] so that the wire leaves each rectangle at 90° to the face
  /// it touches, turns at most once, and rounds that turn.
  ///
  /// Where the two boxes share a band — their y-ranges (or x-ranges) overlap —
  /// a single straight run through the middle of that band satisfies both exits
  /// with no turn at all.  Otherwise there are exactly two single-bend routes:
  /// leave `a` sideways and arrive at `b` from above/below, or the reverse.
  /// Both are legal; the shorter one wins, which is what makes aligned rows of
  /// nodes route the way a person would draw them.
  _Route _route(Rect a, Rect b) {
    final yLo = max(a.top, b.top), yHi = min(a.bottom, b.bottom);
    final xLo = max(a.left, b.left), xHi = min(a.right, b.right);

    // Straight horizontal run through the shared vertical band.
    if (yHi - yLo > _alignEps && (b.left >= a.right || b.right <= a.left)) {
      final y = (yLo + yHi) / 2;
      final p = b.left >= a.right
          ? [Offset(a.right, y), Offset(b.left, y)]
          : [Offset(a.left, y), Offset(b.right, y)];
      return _Route(_straight(p[0], p[1]), (p[0] + p[1]) / 2, true);
    }

    // Straight vertical run through the shared horizontal band.
    if (xHi - xLo > _alignEps && (b.top >= a.bottom || b.bottom <= a.top)) {
      final x = (xLo + xHi) / 2;
      final p = b.top >= a.bottom
          ? [Offset(x, a.bottom), Offset(x, b.top)]
          : [Offset(x, a.top), Offset(x, b.bottom)];
      return _Route(_straight(p[0], p[1]), (p[0] + p[1]) / 2, false);
    }

    // Two boxes sitting on top of each other have no clean route; anything
    // orthogonal would run through one of them anyway. Only reachable when the
    // layout overlaps nodes.
    if ((b.center - a.center).distance < _alignEps) {
      return _Route(_straight(a.center, b.center), a.center, true);
    }

    final rightOf = b.center.dx > a.center.dx;
    final below = b.center.dy > a.center.dy;

    // Candidate 1: out of a's left/right face, into b's top/bottom face.
    final a1 = Offset(rightOf ? a.right : a.left, a.center.dy);
    final b1 = Offset(b.center.dx, below ? b.top : b.bottom);
    final c1 = Offset(b1.dx, a1.dy);
    final len1 = (c1 - a1).distance + (b1 - c1).distance;

    // Candidate 2: out of a's top/bottom face, into b's left/right face.
    final a2 = Offset(a.center.dx, below ? a.bottom : a.top);
    final b2 = Offset(rightOf ? b.left : b.right, b.center.dy);
    final c2 = Offset(a2.dx, b2.dy);
    final len2 = (c2 - a2).distance + (b2 - c2).distance;

    if (len1 <= len2) {
      final (mid, horiz) = _midOf(a1, c1, b1, len1);
      return _Route(_elbow(a1, c1, b1), mid, horiz);
    }
    final (mid, horiz) = _midOf(a2, c2, b2, len2);
    return _Route(_elbow(a2, c2, b2), mid, horiz);
  }

  /// Half-way along the two legs, so the label sits on the wire rather than at
  /// the chord midpoint (which for an L route can land inside a node).
  /// Also reports whether that point is on a horizontal leg.
  (Offset, bool) _midOf(Offset a, Offset corner, Offset b, double total) {
    final leg1 = (corner - a).distance;
    final half = total / 2;
    if (half <= leg1) {
      final p = Offset.lerp(a, corner, leg1 == 0 ? 0 : half / leg1)!;
      return (p, (corner.dy - a.dy).abs() < _alignEps);
    }
    final leg2 = (b - corner).distance;
    final p = Offset.lerp(corner, b, leg2 == 0 ? 0 : (half - leg1) / leg2)!;
    return (p, (b.dy - corner.dy).abs() < _alignEps);
  }

  /// The routing rule is the whole of issue #30, so it is reachable without a
  /// canvas: [connection_routing_test.dart] checks the exit angles and the
  /// single-bend property directly.
  @visibleForTesting
  Path routeBetween(Rect a, Rect b) => _route(a, b).path;

  @visibleForTesting
  Offset labelPointBetween(Rect a, Rect b) => _route(a, b).labelPoint;

  // ── Animated dashes ────────────────────────────────────────────────────────
  final Paint _dashPaint = Paint()
    ..style = PaintingStyle.stroke
    ..strokeWidth = 2.0
    ..strokeCap = StrokeCap.round;

  void _drawDashes(Canvas canvas, Path path, Color color, double phase) {
    // PathMetrics is a one-shot iterable — use for-in, not isEmpty+first.
    // A route is one contour: lineTo/conicTo extend it, only moveTo would break it.
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
  /// Nudges a chip of [size] centred on [canvasPoint] until it fits inside the
  /// viewport.
  ///
  /// The hover chip names every net on the link, so an enriched HyperBus or
  /// JTAG edge produces one wider than the topology panel. Centred on the wire
  /// it ran off both sides and was clipped by the panel border, which is how it
  /// first showed up on screen.
  Offset _keepOnScreen(Offset canvasPoint, Size chip, Size viewport) {
    if (viewport.isEmpty) return canvasPoint;
    final scale = transform.storage[0];
    final halfW = chip.width * scale / 2 + 4;
    final halfH = chip.height * scale / 2 + 4;
    final screen = MatrixUtils.transformPoint(transform, canvasPoint);
    // A chip wider than the viewport cannot be made to fit; pin it to the left
    // edge so the start of the text is readable rather than its middle.
    final x = halfW * 2 >= viewport.width
        ? halfW
        : screen.dx.clamp(halfW, viewport.width - halfW);
    final y = halfH * 2 >= viewport.height
        ? halfH
        : screen.dy.clamp(halfH, viewport.height - halfH);
    return MatrixUtils.transformPoint(Matrix4.inverted(transform), Offset(x, y));
  }

  void _drawLabelChip(Canvas canvas, String label, Offset pos, Size viewport) {
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
    );
    // Wrap rather than run off. An enriched edge names its interface, voltage
    // and every net, which on one line is wider than the topology panel; the
    // text breaks at the spaces between net names.
    final scale = transform.storage[0];
    final room = viewport.isEmpty
        ? 260.0
        : ((viewport.width - 24) / scale).clamp(120.0, 260.0);
    tp.layout(maxWidth: room);

    const pad = 4.0;
    pos = _keepOnScreen(
        pos, Size(tp.width + pad * 2, tp.height + pad * 2), viewport);
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

    // Route once, then use the same geometry for hit-testing and for stroking.
    final routed = <NodeConnection, _Route>{};
    for (final conn in connections) {
      final from = nodes[conn.fromId];
      final to = nodes[conn.toId];
      if (from == null || to == null) continue;
      routed[conn] = _route(_rectOf(from), _rectOf(to));
    }

    NodeConnection? hovered;
    if (hoverPoint != null) {
      for (final entry in routed.entries) {
        if (_isNearPath(entry.value.path, hoverPoint!, 8.0)) {
          hovered = entry.key;
          break;
        }
      }
    }

    for (final entry in routed.entries) {
      final conn = entry.key;
      final route = entry.value;
      final from = nodes[conn.fromId]!;
      final to = nodes[conn.toId]!;
      final isHovered = identical(conn, hovered);

      final active = conn.active &&
          from.status != NodeStatus.disconnected &&
          to.status != NodeStatus.disconnected;

      Color baseColor = active
          ? theme.nodeAccent(from.type).withValues(alpha: isHovered ? 1.0 : 0.8)
          : theme.borderColor.withValues(alpha: 0.5);

      double strokeWidth = from.isPhysical && to.isPhysical ? 2.0 : 1.5;

      if (isHovered) strokeWidth += 0.5;

      // Fault highlight
      if (from.status == NodeStatus.error || to.status == NodeStatus.error) {
        baseColor = theme.statusColor(NodeStatus.error).withValues(alpha: 0.7);
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
        ..strokeJoin = StrokeJoin.round
        ..color = baseColor;

      canvas.drawPath(route.path, paint);

      // Animated dashes on active data connections
      if (conn.dataActive && active) {
        _drawDashes(canvas, route.path, baseColor, animPhase);
      }

      // Hover shows the full interface description if the extractor supplied
      // one; otherwise the short label, which is all a hand-written board file
      // has.
      if (isHovered) {
        final detail = conn.hoverLabel;
        if (detail.isNotEmpty) {
          _drawLabelChip(canvas, detail, route.labelPoint, size);
        }
      } else if (conn.label.isNotEmpty) {
        // Faint static label on the wire.
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
        tp.paint(canvas,
            route.labelPoint - Offset(tp.width / 2, tp.height / 2));
      }
    }

    canvas.restore();
  }

  @override
  bool shouldRepaint(ConnectionPainter old) =>
      old.nodes != nodes ||
      old.connections != connections ||
      old.dragOffsets != dragOffsets ||
      old.animPhase != animPhase ||
      old.hoverPoint != hoverPoint;
}
