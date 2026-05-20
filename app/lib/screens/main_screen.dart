import 'package:flutter/material.dart';
import '../theme.dart' as theme;
import '../widgets/topology/topology_canvas.dart';
import '../widgets/log_panel.dart';
import '../widgets/power_panel.dart';

class MainScreen extends StatelessWidget {
  const MainScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: theme.bgCanvas,
      body: Column(children: [
        _TitleBar(),
        Expanded(
          child: Row(children: [
            // ── Left: topology graph ──────────────────────────────
            Expanded(
              flex: 3,
              child: Container(
                decoration: const BoxDecoration(
                  border: Border(right: BorderSide(color: theme.borderColor)),
                ),
                child: const TopologyCanvas(),
              ),
            ),
            // ── Right: log + power panels ─────────────────────────
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

class _TitleBar extends StatelessWidget {
  @override
  Widget build(BuildContext context) => Container(
        height: 36,
        padding: const EdgeInsets.symmetric(horizontal: 14),
        decoration: const BoxDecoration(
          color: theme.bgPanel,
          border: Border(bottom: BorderSide(color: theme.borderColor)),
        ),
        child: Row(children: [
          Container(
            width: 8,
            height: 8,
            decoration: const BoxDecoration(
              color: Color(0xFF3FB950),
              shape: BoxShape.circle,
            ),
          ),
          const SizedBox(width: 8),
          const Text(
            'Cynthion Monitor',
            style: TextStyle(
              color: theme.textPrimary,
              fontSize: 12,
              fontWeight: FontWeight.w600,
              letterSpacing: 0.5,
            ),
          ),
          const SizedBox(width: 12),
          const Text(
            '— stub data, apollod not connected',
            style: TextStyle(color: theme.textMuted, fontSize: 10),
          ),
        ]),
      );
}
