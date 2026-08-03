import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../models/tty_line.dart';
import '../providers/tty_provider.dart';
import '../theme.dart' as theme;

Color _srcColor(TtySource src) => switch (src) {
      TtySource.rv0    => const Color(0xFF3FB950),
      TtySource.fpg    => const Color(0xFF79C0FF),
      TtySource.apl    => const Color(0xFF8B949E),
      TtySource.system => const Color(0xFFF78166),
    };

class TerminalPanel extends ConsumerStatefulWidget {
  final String title;
  final Set<TtySource> sources;
  final int maxLines;

  const TerminalPanel({
    required this.title,
    required this.sources,
    this.maxLines = 100,
    super.key,
  });

  @override
  ConsumerState<TerminalPanel> createState() => _TerminalPanelState();
}

class _TerminalPanelState extends ConsumerState<TerminalPanel> {
  final _scroll = ScrollController();
  bool _autoScroll = true;

  @override
  void dispose() {
    _scroll.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final all = ref.watch(ttyProvider);
    final lines = all
        .where((l) => widget.sources.contains(l.source))
        .toList()
        .skip(all.length > widget.maxLines ? all.length - widget.maxLines : 0)
        .toList();

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
          child: lines.isEmpty
              ? Center(
                  child: Text(
                    'Waiting for data…',
                    style: TextStyle(color: theme.textMuted.withValues(alpha: 0.5)),
                  ),
                )
              : ListView.builder(
                  controller: _scroll,
                  padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 3),
                  itemCount: lines.length,
                  itemBuilder: (_, i) => _TerminalLine(line: lines[i]),
                ),
        ),
      ),
    ]);
  }

  Widget _header() => Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
        decoration: const BoxDecoration(
          border: Border(bottom: BorderSide(color: theme.borderColor)),
        ),
        child: Row(children: [
          const Icon(Icons.terminal, size: 12, color: theme.textMuted),
          const SizedBox(width: 5),
          Text(
            widget.title,
            style: const TextStyle(color: theme.textMuted, fontSize: 10),
          ),
          const Spacer(),
          if (!_autoScroll)
            GestureDetector(
              onTap: () {
                setState(() => _autoScroll = true);
                _scroll.jumpTo(_scroll.position.maxScrollExtent);
              },
              child: const Text('↓',
                  style: TextStyle(color: Color(0xFF58A6FF), fontSize: 9)),
            ),
        ]),
      );
}

class _TerminalLine extends StatelessWidget {
  final TtyLine line;
  const _TerminalLine({required this.line});

  @override
  Widget build(BuildContext context) {
    final srcColor = _srcColor(line.source);
    final ts = line.timestamp;
    final timeStr =
        '${ts.hour.toString().padLeft(2, '0')}:${ts.minute.toString().padLeft(2, '0')}:${ts.second.toString().padLeft(2, '0')}';

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 0.5),
      child: RichText(
        maxLines: 2,
        overflow: TextOverflow.ellipsis,
        text: TextSpan(
          style: const TextStyle(fontSize: 9, fontFamily: 'monospace', height: 1.3),
          children: [
            TextSpan(text: '$timeStr ', style: TextStyle(color: theme.textMuted.withValues(alpha: 0.4))),
            TextSpan(
              text: line.isFault ? '⚠ ' : '• ',
              style: TextStyle(color: srcColor, fontWeight: FontWeight.bold),
            ),
            TextSpan(
              text: line.text,
              style: TextStyle(
                color: line.isFault ? const Color(0xFFF85149) : theme.textPrimary.withValues(alpha: 0.75),
                fontWeight: line.isFault ? FontWeight.w600 : FontWeight.normal,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
