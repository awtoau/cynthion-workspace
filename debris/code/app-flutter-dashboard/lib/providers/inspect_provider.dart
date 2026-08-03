import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

const _prefKey = 'inspect_enabled_v1';

class InspectNotifier extends StateNotifier<bool> {
  InspectNotifier() : super(false) {
    _loadEnabled();
  }

  Future<void> _loadEnabled() async {
    final prefs = await SharedPreferences.getInstance();
    state = prefs.getBool(_prefKey) ?? false;
  }

  Future<void> setEnabled(bool enabled) async {
    state = enabled;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool(_prefKey, enabled);
  }

  void toggle() => setEnabled(!state);
}

final inspectProvider = StateNotifierProvider<InspectNotifier, bool>(
  (ref) => InspectNotifier(),
);
