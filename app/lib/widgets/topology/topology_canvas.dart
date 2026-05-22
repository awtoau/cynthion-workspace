import 'dart:io';
import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../models/node.dart';
import '../../providers/board_config_provider.dart';
import '../../providers/topology_provider.dart';
import '../../theme.dart' as theme;
import 'connection_painter.dart';
import 'hardware_node_widget.dart';
import 'pinout_dialog.dart';

const _canvasW = 1200.0;
const _canvasH = 750.0;

class TopologyCanvas extends ConsumerStatefulWidget {
  const TopologyCanvas({super.key});

  @override
  ConsumerState<TopologyCanvas> createState() => _TopologyCanvasState();
}

class _TopologyCanvasState extends ConsumerState<TopologyCanvas>
    with SingleTickerProviderStateMixin {
  final _transform = TransformationController();
  String? _selectedId;
  String? _hoveredNodeId;
  // Real mouse position in canvas-space, updated without setState so the
  // AnimatedBuilder picks it up each frame without a full tree rebuild.
  Offset? _mouseCanvasPos;

  String? _draggingNodeId;

  late final AnimationController _animController;

  @override
  void initState() {
    super.initState();
    _animController = AnimationController(
      vsync: this,
      duration: const Duration(seconds: 2),
    )..repeat();
  }

  @override
  void dispose() {
    _animController.dispose();
    _transform.dispose();
    super.dispose();
  }

  void _onNodeHover(String? id) {
    _dbgLog('node_hover id=$id prev=$_hoveredNodeId mouse=$_mouseCanvasPos');
    setState(() => _hoveredNodeId = id);
  }

  static void _dbgLog(String msg) {
    try {
      File('/home/dan/git/cynthion-workspace/app/tmp/connection_debug.log').writeAsStringSync(
        '${DateTime.now().toIso8601String().substring(11, 23)} [canvas] $msg\n',
        mode: FileMode.append,
      );
    } catch (_) {}
  }

  void _onCanvasMouseMove(PointerHoverEvent event) {
    // Invert the InteractiveViewer transform to get canvas-space coordinates.
    final matrix = Matrix4.inverted(_transform.value);
    _mouseCanvasPos = MatrixUtils.transformPoint(matrix, event.localPosition);
    // No setState — AnimatedBuilder repaints every frame, picks up the new value.
  }

  void _onNodeTap(HardwareNode node) {
    setState(() {
      _selectedId = _selectedId == node.id ? null : node.id;
    });
    final info = node.info;
    if (info != null && info.pins.isNotEmpty) {
      showDialog<void>(
        context: context,
        builder: (_) => PinoutDialog(node: node),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final nodes = ref.watch(topologyProvider);
    final connections = ref.watch(connectionsProvider);
    final hoveredNode = _hoveredNodeId != null ? nodes[_hoveredNodeId] : null;

    // AnimatedBuilder is outermost so animation ticks never cause the
    // InteractiveViewer to be torn down by an unrelated setState.
    return AnimatedBuilder(
      animation: _animController,
      builder: (context, _) => LayoutBuilder(
        builder: (context, constraints) {
          final w = constraints.maxWidth.isFinite ? constraints.maxWidth : _canvasW;
          final h = constraints.maxHeight.isFinite ? constraints.maxHeight : _canvasH;
          final canvasSize = Size(w, h);

          return MouseRegion(
            // opaque:false lets node MouseRegions still work normally while
            // this region tracks the cursor for connection-hover detection.
            opaque: false,
            onHover: _onCanvasMouseMove,
            onExit: (_) => _mouseCanvasPos = null,
            child: Stack(children: [
              // ── Connection layer ──────────────────────────────────
              // Lives OUTSIDE InteractiveViewer so node-hover setState
              // never invalidates or re-clips this layer. Transform from
              // TransformationController is applied inside paint() directly.
              Positioned.fill(
                child: CustomPaint(
                  painter: ConnectionPainter(
                    nodes: nodes,
                    connections: connections,
                    animPhase: _animController.value,
                    hoverPoint: _mouseCanvasPos,
                    transform: _transform.value,
                  ),
                ),
              ),

              // ── Interactive canvas (nodes only) ──────────────────
              // constrained:false gives child infinite layout constraints so
              // the fixed-size canvas can extend beyond the viewport and nodes
              // remain reachable by panning. Clip.none prevents the inner Stack
              // from clipping nodes that sit near the canvas edge.
              SizedBox.expand(
                child: InteractiveViewer(
                  transformationController: _transform,
                  constrained: false,
                  panEnabled: _draggingNodeId == null,
                  scaleEnabled: _draggingNodeId == null,
                  boundaryMargin: const EdgeInsets.all(300),
                  minScale: 0.25,
                  maxScale: 4.0,
                  child: SizedBox(
                    width: 1100,
                    height: 750,
                    child: Stack(
                      clipBehavior: Clip.none,
                      children: [
                    ...nodes.values.map((node) => Positioned(
                          left: node.position.dx,
                          top: node.position.dy,
                          child: Listener(
                            behavior: HitTestBehavior.opaque,
                            onPointerDown: (_) =>
                                setState(() => _draggingNodeId = node.id),
                            onPointerMove: (e) {
                              if (_draggingNodeId == node.id) {
                                // e.delta is screen pixels; divide by viewer
                                // scale to get canvas-space displacement.
                                final scale = _transform.value.storage[0];
                                ref
                                    .read(topologyProvider.notifier)
                                    .moveNode(node.id, e.delta / scale);
                              }
                            },
                            onPointerUp: (_) {
                              if (_draggingNodeId == node.id) {
                                setState(() => _draggingNodeId = null);
                              }
                            },
                            onPointerCancel: (_) =>
                                setState(() => _draggingNodeId = null),
                            child: GestureDetector(
                              onTap: () => _onNodeTap(node),
                              child: HardwareNodeWidget(
                                node: node,
                                selected: _selectedId == node.id,
                                hovered: _hoveredNodeId == node.id,
                                onHoverChanged: _onNodeHover,
                              ),
                            ),
                          ),
                        )),
                      ]),
                    ),
                  ),
                ),

              // ── Tooltip overlay ─────────────────────────────────
              if (hoveredNode != null) _buildTooltip(hoveredNode),

              // ── Toolbar overlay ─────────────────────────────────
              Positioned(
                bottom: 12,
                right: 12,
                child: _Toolbar(
                  onAutoLayout: () => ref
                      .read(topologyProvider.notifier)
                      .autoLayout(canvasSize),
                  onResetZoom: () => _transform.value = Matrix4.identity(),
                ),
              ),
            ]),
          );
        },
      ),
    );
  }

  Widget _buildTooltip(HardwareNode node) {
    final info = node.info;
    if (info == null) return const SizedBox.shrink();

    final accent = theme.nodeAccent(node.type);
    final hasPins = info.pins.isNotEmpty;

    return Positioned(
      top: 12,
      left: 12,
      child: IgnorePointer(
        child: Container(
          constraints: const BoxConstraints(maxWidth: 260),
          padding: const EdgeInsets.all(10),
          decoration: BoxDecoration(
            color: theme.bgCard,
            borderRadius: BorderRadius.circular(8),
            border: Border.all(color: accent.withValues(alpha: 0.4)),
            boxShadow: [
              BoxShadow(
                color: Colors.black.withValues(alpha: 0.4),
                blurRadius: 8,
                offset: const Offset(0, 2),
              ),
            ],
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              if (info.partNumber.isNotEmpty)
                Text(
                  info.partNumber,
                  style: TextStyle(
                    color: accent,
                    fontSize: 11,
                    fontWeight: FontWeight.bold,
                    fontFamily: 'monospace',
                  ),
                ),
              if (info.manufacturer.isNotEmpty)
                Text(
                  info.manufacturer,
                  style: const TextStyle(
                    color: theme.textMuted,
                    fontSize: 9,
                  ),
                ),
              if (info.partNumber.isNotEmpty || info.manufacturer.isNotEmpty)
                const SizedBox(height: 4),
              Text(
                info.description,
                style: const TextStyle(
                  color: theme.textPrimary,
                  fontSize: 10,
                ),
              ),
              if (hasPins) ...[
                const SizedBox(height: 6),
                Text(
                  'Tap for pinout',
                  style: TextStyle(
                    color: accent.withValues(alpha: 0.7),
                    fontSize: 9,
                    fontStyle: FontStyle.italic,
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

// ── Toolbar ───────────────────────────────────────────────────────────────────
class _Toolbar extends StatelessWidget {
  final VoidCallback onAutoLayout;
  final VoidCallback onResetZoom;

  const _Toolbar({required this.onAutoLayout, required this.onResetZoom});

  @override
  Widget build(BuildContext context) => Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          _btn(Icons.auto_fix_high, 'Auto layout', onAutoLayout),
          const SizedBox(width: 6),
          _btn(Icons.fit_screen, 'Reset zoom', onResetZoom),
        ],
      );

  Widget _btn(IconData icon, String tooltip, VoidCallback onTap) => Tooltip(
        message: tooltip,
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(6),
          child: Container(
            padding: const EdgeInsets.all(7),
            decoration: BoxDecoration(
              color: theme.bgCard,
              border: Border.all(color: theme.borderColor),
              borderRadius: BorderRadius.circular(6),
            ),
            child: Icon(icon, size: 16, color: theme.textMuted),
          ),
        ),
      );
}
