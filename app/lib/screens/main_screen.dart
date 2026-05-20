import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../providers/transport_provider.dart';
import '../services/transport/apollo_transport.dart';
import '../theme.dart' as theme;
import '../widgets/topology/topology_canvas.dart';
import '../widgets/log_panel.dart';
import '../widgets/power_panel.dart';
import 'connect_screen.dart';

class MainScreen extends StatelessWidget {
  const MainScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: theme.bgCanvas,
      body: Column(children: [
        const _TitleBar(),
        Expanded(
          child: Row(children: [
            Expanded(
              flex: 3,
              child: Container(
                decoration: const BoxDecoration(
                  border: Border(right: BorderSide(color: theme.borderColor)),
                ),
                child: const TopologyCanvas(),
              ),
            ),
            SizedBox(
              width: 360,
              child: Column(children: [
                Expanded(
                  flex: 3,
                  child: Container(
                    decoration: const BoxDecoration(
                      color: theme.bgPanel,
                      border: Border(bottom: BorderSide(color: theme.borderColor)),
                    ),
                    child: const LogPanel(),
                  ),
                ),
                SizedBox(
                  height: 170,
                  child: Container(
                    color: theme.bgPanel,
                    child: const PowerPanel(),
                  ),
                ),
              ]),
            ),
          ]),
        ),
      ]),
    );
  }
}

class _TitleBar extends ConsumerWidget {
  const _TitleBar();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final transport = ref.watch(transportProvider);
    final connected = transport?.state == TransportState.connected;
    final connecting = transport?.state == TransportState.connecting;

    final dotColor = connected
        ? const Color(0xFF3FB950)
        : connecting
            ? const Color(0xFFD29922)
            : const Color(0xFF484F58);

    final statusText = connected
        ? transport!.displayName
        : connecting
            ? 'connecting…'
            : 'stub data · apollod not connected';

    return Container(
      height: 36,
      padding: const EdgeInsets.symmetric(horizontal: 14),
      decoration: const BoxDecoration(
        color: theme.bgPanel,
        border: Border(bottom: BorderSide(color: theme.borderColor)),
      ),
      child: Row(children: [
        Container(
          width: 8, height: 8,
          decoration: BoxDecoration(
            color: dotColor,
            shape: BoxShape.circle,
            boxShadow: connected
                ? [BoxShadow(color: dotColor.withValues(alpha: 0.5), blurRadius: 5)]
                : null,
          ),
        ),
        const SizedBox(width: 8),
        const Text('Cynthion Monitor',
            style: TextStyle(
                color: theme.textPrimary, fontSize: 12,
                fontWeight: FontWeight.w600, letterSpacing: 0.5)),
        const SizedBox(width: 10),
        Text('— $statusText',
            style: const TextStyle(color: theme.textMuted, fontSize: 10)),
        const Spacer(),
        _connectBtn(context, ref, connected),
      ]),
    );
  }

  Widget _connectBtn(BuildContext context, WidgetRef ref, bool connected) {
    return TextButton(
      onPressed: () {
        if (connected) {
          ref.read(transportProvider.notifier).disconnect();
        } else {
          showDialog(
            context: context,
            builder: (_) => Dialog(
              backgroundColor: theme.bgPanel,
              shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(8),
                  side: const BorderSide(color: theme.borderColor)),
              child: const SizedBox(
                  width: 480, height: 500, child: ConnectScreen()),
            ),
          );
        }
      },
      style: TextButton.styleFrom(
        backgroundColor: theme.bgCard,
        side: const BorderSide(color: theme.borderColor),
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(5)),
        minimumSize: Size.zero,
        tapTargetSize: MaterialTapTargetSize.shrinkWrap,
      ),
      child: Text(
        connected ? 'Disconnect' : 'Connect…',
        style: const TextStyle(color: theme.textMuted, fontSize: 11),
      ),
    );
  }
}
