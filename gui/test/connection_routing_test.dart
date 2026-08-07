import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:cynthion_monitor/models/node.dart';
import 'package:cynthion_monitor/widgets/topology/connection_painter.dart';

ConnectionPainter _painter() => ConnectionPainter(
      nodes: const {},
      connections: const [],
      transform: Matrix4.identity(),
    );

/// Tangent direction at a distance along the path, as a unit vector.
Offset _tangentAt(Path path, double t) {
  for (final m in path.computeMetrics()) {
    final tan = m.getTangentForOffset(t.clamp(0.0, m.length));
    if (tan != null) return tan.vector;
  }
  fail('path has no contour');
}

void _expectAxisAligned(Offset v, String where) {
  final off = min(v.dx.abs(), v.dy.abs());
  expect(off, lessThan(0.02),
      reason: '$where should leave along an axis, got $v');
}

/// Every arrangement of two nodes that the canvas can produce. Sizes match the
/// real node boxes so the corner radius is exercised at its clamped and
/// unclamped extremes.
const _a = Rect.fromLTWH(100, 100, 130, 62);

void main() {
  group('connection routing', () {
    for (final entry in {
      'diagonal, far': const Rect.fromLTWH(500, 400, 130, 62),
      'diagonal, near': const Rect.fromLTWH(260, 200, 130, 62),
      'up and left': const Rect.fromLTWH(-200, -150, 130, 62),
      'mostly right': const Rect.fromLTWH(600, 130, 130, 62),
      'mostly below': const Rect.fromLTWH(120, 500, 130, 62),
      'compact node': const Rect.fromLTWH(420, 330, 100, 48),
    }.entries) {
      test('${entry.key}: leaves both nodes at 90°', () {
        final path = _painter().routeBetween(_a, entry.value);
        final metric = path.computeMetrics().first;
        _expectAxisAligned(_tangentAt(path, 0), 'start');
        _expectAxisAligned(_tangentAt(path, metric.length), 'end');
      });

      test('${entry.key}: never longer than the Manhattan detour', () {
        final path = _painter().routeBetween(_a, entry.value);
        final metric = path.computeMetrics().first;
        final start = metric.getTangentForOffset(0)!.position;
        final end = metric.getTangentForOffset(metric.length)!.position;
        final manhattan =
            (end.dx - start.dx).abs() + (end.dy - start.dy).abs();
        // A rounded corner cuts the elbow, so the wire is at most as long as
        // the square route and no shorter than the straight line.
        expect(metric.length, lessThanOrEqualTo(manhattan + 0.01));
        expect(metric.length, greaterThanOrEqualTo((end - start).distance - 0.01));
      });

      test('${entry.key}: turns at most once', () {
        final path = _painter().routeBetween(_a, entry.value);
        final metric = path.computeMetrics().first;
        var turns = 0;
        Offset? previous;
        for (var t = 0.0; t <= metric.length; t += 1.0) {
          final v = _tangentAt(path, t);
          if (previous != null && (v - previous).distance > 0.5) turns++;
          previous = v;
        }
        // One rounded corner sweeps through many small direction changes but
        // only one reversal-free quarter turn; counting large jumps catches a
        // route that zig-zags.
        expect(turns, lessThanOrEqualTo(1),
            reason: 'more than one bend in the route');
      });
    }

    test('aligned nodes get a straight wire, no bend', () {
      // Same vertical band, separated horizontally: nothing to turn around.
      final path = _painter()
          .routeBetween(_a, const Rect.fromLTWH(500, 100, 130, 62));
      final metric = path.computeMetrics().first;
      final start = metric.getTangentForOffset(0)!.position;
      final end = metric.getTangentForOffset(metric.length)!.position;
      expect(start.dy, closeTo(end.dy, 0.01));
      expect(metric.length, closeTo((end.dx - start.dx).abs(), 0.01));
    });

    test('label sits beside the wire, not on it', () {
      const b = Rect.fromLTWH(500, 100, 130, 62);
      final painter = _painter();
      final label = painter.labelPointBetween(_a, b);
      final metric = painter.routeBetween(_a, b).computeMetrics().first;
      final onWire = metric.getTangentForOffset(metric.length / 2)!.position;
      expect((label - onWire).distance, greaterThan(4));
    });
  });

  test('hover chip falls back to the label when the netlist said nothing', () {
    const bare = NodeConnection(fromId: 'a', toId: 'b', label: 'JTAG + UART');
    expect(bare.hoverLabel, 'JTAG + UART');
  });

  test('hover chip shows the extracted interface when there is one', () {
    const rich = NodeConnection(
      fromId: 'fpga',
      toId: 'hyperram',
      label: 'HyperBus',
      interface: 'HyperBus',
      nets: ['RAM.CK', 'RAM.DQ0', 'RAM.DQ1', 'RAM.DQ2', 'RAM.DQ3', 'RAM.DQ4',
             'RAM.DQ5', 'RAM.DQ6'],
      voltage: '1.8 V',
      signalType: 'HyperBus · 1.8 V · 13 nets',
      direction: 'ECP5 FPGA↔HyperRAM',
    );
    expect(rich.hoverLabel, contains('ECP5 FPGA↔HyperRAM'));
    expect(rich.hoverLabel, contains('HyperBus · 1.8 V · 13 nets'));
    // Long net lists are truncated so the chip stays narrower than the canvas.
    expect(rich.hoverLabel, contains('+2 more'));
  });
}
