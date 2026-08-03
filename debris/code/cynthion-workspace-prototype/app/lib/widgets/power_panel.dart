import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../models/node.dart';
import '../providers/power_provider.dart';
import '../theme.dart' as theme;

class PowerPanel extends ConsumerWidget {
  const PowerPanel({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final channels = ref.watch(powerProvider);

    return Column(children: [
      _header(),
      Expanded(
        child: Padding(
          padding: const EdgeInsets.all(8),
          child: Column(
            children: channels
                .map((ch) => Padding(
                      padding: const EdgeInsets.symmetric(vertical: 3),
                      child: _ChannelRow(channel: ch),
                    ))
                .toList(),
          ),
        ),
      ),
    ]);
  }

  Widget _header() => Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
        decoration: const BoxDecoration(
          border: Border(bottom: BorderSide(color: theme.borderColor)),
        ),
        child: Row(children: [
          const Icon(Icons.bolt, size: 13, color: theme.colPower),
          const SizedBox(width: 6),
          const Text('PAC1954 · power rails  [FAKE DATA]',
              style: TextStyle(color: theme.textMuted, fontSize: 11)),
        ]),
      );
}

class _ChannelRow extends StatelessWidget {
  final PowerChannel channel;
  const _ChannelRow({required this.channel});

  @override
  Widget build(BuildContext context) {
    final warn = channel.voltage < 0.9 * _nominal(channel.label);
    final color = warn ? theme.statusColor(NodeStatus.warning) : theme.colPower;

    return Row(children: [
      SizedBox(
        width: 78,
        child: Text(channel.label,
            style: const TextStyle(color: theme.textMuted, fontSize: 10)),
      ),
      _val('${channel.voltage.toStringAsFixed(3)} V', color),
      const SizedBox(width: 8),
      _val('${(channel.current * 1000).toStringAsFixed(0)} mA', theme.textMuted),
      const SizedBox(width: 8),
      _val('${channel.power.toStringAsFixed(3)} W',
          theme.textMuted.withValues(alpha: 0.6)),
    ]);
  }

  Widget _val(String text, Color color) => Text(text,
      style: TextStyle(color: color, fontSize: 11, fontFamily: 'monospace'));

  double _nominal(String label) {
    if (label.contains('5')) return 5.0;
    if (label.contains('3')) return 3.3;
    if (label.contains('1V8')) return 1.8;
    return 1.1;
  }
}

