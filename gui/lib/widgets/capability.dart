import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/device_profile.dart';
import '../providers/device_profile_provider.dart';
import '../theme.dart' as theme;

/// Where a panel's contents come from right now.
enum FeedState {
  /// Real measurements from a device that supports this feature.
  live,

  /// Generated numbers, because nothing is connected.
  demo,

  /// Connected, but this device does not offer the feature.
  unsupported,
}

FeedState feedStateFor(DeviceProfile profile, String cap) {
  if (profile.variant == DeviceVariant.offline) return FeedState.demo;
  return profile.has(cap) ? FeedState.live : FeedState.unsupported;
}

/// Small chip naming the source of a panel's data. Sits in every panel header,
/// so a screenshot never leaves the reader guessing whether the numbers are
/// from the board.
class FeedChip extends ConsumerWidget {
  final String cap;
  const FeedChip({required this.cap, super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final state = feedStateFor(ref.watch(deviceProfileProvider), cap);
    final (text, color) = switch (state) {
      FeedState.live => ('LIVE', const Color(0xFF3FB950)),
      FeedState.demo => ('DEMO', const Color(0xFFD29922)),
      FeedState.unsupported => ('N/A', theme.textMuted),
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.12),
        border: Border.all(color: color.withValues(alpha: 0.5)),
        borderRadius: BorderRadius.circular(3),
      ),
      child: Text(text,
          style: TextStyle(
              color: color,
              fontSize: 8,
              fontFamily: 'monospace',
              letterSpacing: 0.5)),
    );
  }
}

/// Shown in place of a panel's body when the connected device does not offer
/// the feature — the graceful half of "advanced features only when supported".
class UnsupportedNotice extends StatelessWidget {
  final String what;
  const UnsupportedNotice({required this.what, super.key});

  @override
  Widget build(BuildContext context) => Center(
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(Icons.block, size: 16, color: theme.textMuted.withValues(alpha: 0.5)),
              const SizedBox(height: 6),
              Text(
                '$what is not available on this device',
                textAlign: TextAlign.center,
                style: TextStyle(
                    color: theme.textMuted.withValues(alpha: 0.7), fontSize: 10),
              ),
              const SizedBox(height: 2),
              Text(
                'needs the awto fork daemon',
                style: TextStyle(
                    color: theme.textMuted.withValues(alpha: 0.4), fontSize: 9),
              ),
            ],
          ),
        ),
      );
}
