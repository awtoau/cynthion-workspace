import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../models/tty_line.dart';
import '../providers/tty_provider.dart';
import '../theme.dart' as theme;

Color _srcColor(TtySource src) => switch (src) {
      TtySource.rv0    => const Color(0xFF3FB950), // green — RISC-V
      TtySource.fpg    => const Color(0xFF79C0FF), // blue  — FPGA events
      TtySource.apl    => const Color(0xFF8B949E), // grey  — Apollo console
      TtySource.system => const Color(0xFFF78166), // coral — system
    };

class LogPanel extends ConsumerStatefulWidget {
  const LogPanel({super.key});

  @override
  ConsumerState<LogPanel> createState() => _LogPanelState();
}

class _LogPanelState extends ConsumerState<LogPanel> {
  final _scroll = ScrollController();
  bool _autoScroll = true;
  final Set<TtySource> _visible = {
    TtySource.rv0, TtySource.fpg, TtySource.apl, TtySource.system,
  };

  @override
  void dispose() {
    _scroll.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final all = ref.watch(ttyProvider);
    final lines = all.where((l) => _visible.contains(l.source)).toList();

    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_autoScroll && _scroll.hasClients) {
        _scroll.jumpTo(_scroll.position.maxScrollExtent);
      }
    });

    return Column(children: [
      _header(),
      Expanded(
        child: NotificationListener<ScrollNotification>(
          onNotification: (n) {
            if (n is ScrollUpdateNotification) {
              final atBottom = _scroll.position.pixels >=
                  _scroll.position.maxScrollExtent - 40;
              if (_autoScroll != atBottom) {
                setState(() => _autoScroll = atBottom);
              }
            }
            return false;
          },
          child: ListView.builder(
            controller: _scroll,
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
            itemCount: lines.length,
            itemBuilder: (_, i) => _LogLine(line: lines[i]),
          ),
        ),
      ),
    ]);
  }

  Widget _header() => Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
        decoration: const BoxDecoration(
          border: Border(bottom: BorderSide(color: theme.borderColor)),
        ),
        child: Row(children: [
          const Icon(Icons.terminal, size: 13, color: theme.textMuted),
          const SizedBox(width: 6),
          const Text('TTY log',
              style: TextStyle(color: theme.textMuted, fontSize: 11)),
          const SizedBox(width: 10),
          ...TtySource.values.map((src) => _SourceChip(
                src: src,
                on: _visible.contains(src),
                onTap: () => setState(() {
                  if (_visible.contains(src)) {
                    if (_visible.length > 1) _visible.remove(src);
                  } else {
                    _visible.add(src);
                  }
                }),
              )),
          const Spacer(),
          if (!_autoScroll)
            GestureDetector(
              onTap: () {
                setState(() => _autoScroll = true);
                _scroll.jumpTo(_scroll.position.maxScrollExtent);
              },
              child: const Text('↓ live',
                  style: TextStyle(color: Color(0xFF58A6FF), fontSize: 10)),
            ),
        ]),
      );
}

class _SourceChip extends StatelessWidget {
  final TtySource src;
  final bool on;
  final VoidCallback onTap;
  const _SourceChip({required this.src, required this.on, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final color = _srcColor(src);
    final tag = switch (src) {
      TtySource.rv0    => 'rv0',
      TtySource.fpg    => 'fpg',
      TtySource.apl    => 'apl',
      TtySource.system => 'sys',
    };
    return GestureDetector(
      onTap: onTap,
      child: Container(
        margin: const EdgeInsets.only(right: 4),
        padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 2),
        decoration: BoxDecoration(
          color: on ? color.withValues(alpha: 0.15) : Colors.transparent,
          border: Border.all(
              color: on ? color.withValues(alpha: 0.6) : theme.borderColor),
          borderRadius: BorderRadius.circular(3),
        ),
        child: Text(tag,
            style: TextStyle(
              color: on ? color : theme.textMuted,
              fontSize: 9,
              fontFamily: 'monospace',
              fontWeight: FontWeight.w600,
            )),
      ),
    );
  }
}

class _LogLine extends StatelessWidget {
  final TtyLine line;
  const _LogLine({required this.line});

  @override
  Widget build(BuildContext context) {
    final srcColor = _srcColor(line.source);
    final ts = line.timestamp;
    final timeStr =
        '${ts.hour.toString().padLeft(2,'0')}:${ts.minute.toString().padLeft(2,'0')}:${ts.second.toString().padLeft(2,'0')}.${(ts.millisecond ~/ 10).toString().padLeft(2,'0')}';

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 1),
      child: RichText(
        text: TextSpan(
          style: const TextStyle(fontSize: 11, fontFamily: 'monospace', height: 1.4),
          children: [
            TextSpan(text: '$timeStr ', style: TextStyle(color: theme.textMuted.withValues(alpha: 0.5))),
            TextSpan(
              text: '[${line.sourceTag}] ',
              style: TextStyle(color: srcColor, fontWeight: FontWeight.w600),
            ),
            TextSpan(
              text: line.text,
              style: TextStyle(
                color: line.isFault ? const Color(0xFFF85149) : theme.textPrimary.withValues(alpha: 0.85),
                fontWeight: line.isFault ? FontWeight.w600 : FontWeight.normal,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
