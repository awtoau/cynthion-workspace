import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../models/node.dart';
import 'board_config_provider.dart';

const _prefKey = 'node_positions_v1';

class TopologyNotifier extends Notifier<Map<String, HardwareNode>> {
  @override
  Map<String, HardwareNode> build() {
    final nodes = ref.watch(boardConfigProvider).valueOrNull?.nodes ?? const [];
    _loadPositions();
    return {for (final n in nodes) n.id: n};
  }

  void moveNode(String id, Offset delta) {
    final node = state[id];
    if (node == null) return;
    final updated = node.copyWith(position: node.position + delta);
    state = {...state, id: updated};
    _savePositions();
  }

  void setStatus(String id, NodeStatus status) {
    final node = state[id];
    if (node == null) return;
    state = {...state, id: node.copyWith(status: status)};
  }

  // Layered auto-layout: assigns positions by logical depth from host.
  void autoLayout(Size canvasSize) {
    const layerX = [60.0, 300.0, 560.0, 820.0, 1060.0];
    final layers = <int, List<String>>{
      0: ['host', 'apollod', 'facedancer'],
      1: ['apollo', 'pac1954'],
      2: ['fpga', 'riscv', 'moondancer', 'gateware'],
      3: ['targetA', 'targetC'],
      4: ['camera'],
    };

    final updated = Map<String, HardwareNode>.from(state);
    for (final entry in layers.entries) {
      final ids = entry.value;
      final x = layerX[entry.key];
      final step = canvasSize.height / (ids.length + 1);
      for (var i = 0; i < ids.length; i++) {
        final id = ids[i];
        if (updated.containsKey(id)) {
          updated[id] = updated[id]!.copyWith(
            position: Offset(x, step * (i + 1) - nodeSize.height / 2),
          );
        }
      }
    }
    state = updated;
    _savePositions();
  }

  Future<void> _savePositions() async {
    final prefs = await SharedPreferences.getInstance();
    final map = {
      for (final e in state.entries)
        e.key: {'x': e.value.position.dx, 'y': e.value.position.dy}
    };
    await prefs.setString(_prefKey, jsonEncode(map));
  }

  Future<void> _loadPositions() async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString(_prefKey);
    if (raw == null) return;
    try {
      final map = jsonDecode(raw) as Map<String, dynamic>;
      final updated = Map<String, HardwareNode>.from(state);
      for (final e in map.entries) {
        if (updated.containsKey(e.key)) {
          updated[e.key] = updated[e.key]!.copyWith(
            position: Offset(
              (e.value['x'] as num).toDouble(),
              (e.value['y'] as num).toDouble(),
            ),
          );
        }
      }
      state = updated;
    } catch (_) {}
  }
}

final topologyProvider =
    NotifierProvider<TopologyNotifier, Map<String, HardwareNode>>(
        TopologyNotifier.new);
