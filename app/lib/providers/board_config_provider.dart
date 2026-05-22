import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../models/board_config.dart';
import '../models/node.dart';

final boardConfigProvider = FutureProvider<BoardConfig>(
    (ref) => BoardConfig.loadAsset('assets/hardware/cynthion.json'));

final connectionsProvider = Provider<List<NodeConnection>>(
    (ref) => ref.watch(boardConfigProvider).valueOrNull?.connections ?? const []);
