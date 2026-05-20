enum TtySource { rv0, fpg, apl, system }

class TtyLine {
  final TtySource source;
  final DateTime timestamp;
  final String text;
  final bool isFault;

  const TtyLine({
    required this.source,
    required this.timestamp,
    required this.text,
    this.isFault = false,
  });

  String get sourceTag => switch (source) {
        TtySource.rv0 => 'rv0',
        TtySource.fpg => 'fpg',
        TtySource.apl => 'apl',
        TtySource.system => 'sys',
      };
}
