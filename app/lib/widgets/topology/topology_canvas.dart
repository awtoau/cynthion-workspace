import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../models/node.dart';
import '../../providers/topology_provider.dart';
import '../../theme.dart' as theme;
import 'connection_painter.dart';
import 'hardware_node_widget.dart';

const _canvasW = 1300.0;
const _canvasH = 700.0;

class TopologyCanvas extends ConsumerStatefulWidget {
  const TopologyCanvas({super.key});

  @override
  ConsumerState<TopologyCanvas> createState() => _TopologyCanvasState();
}

class _TopologyCanvasState extends ConsumerState<TopologyCanvas> {
  final _transform = TransformationController();
  String? _selectedId;
  // Track per-node drag start in canvas coords
  final Map<String, Offset> _dragStart = {};

  @override
  void dispose() {
    _transform.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final nodes = ref.watch(topologyProvider);

    return Stack(children: [
      InteractiveViewer(
        transformationController: _transform,
        boundaryMargin: const EdgeInsets.all(200),
        minScale: 0.4,
        maxScale: 3.0,
        child: SizedBox(
          width: _canvasW,
          height: _canvasH,
          child: Stack(children: [
            // Connection layer
            CustomPaint(
              size: const Size(_canvasW, _canvasH),
              painter: ConnectionPainter(
                nodes: nodes,
                connections: defaultConnections,
              ),
            ),
            // Node layer
            ...nodes.values.map((node) => Positioned(
                  left: node.position.dx,
                  top: node.position.dy,
                  child: GestureDetector(
                    onPanStart: (d) {
                      _dragStart[node.id] = d.localPosition;
                    },
                    onPanUpdate: (d) {
                      final delta = d.localPosition - (_dragStart[node.id] ?? d.localPosition);
                      _dragStart[node.id] = d.localPosition;
                      ref.read(topologyProvider.notifier).moveNode(node.id, delta);
                    },
                    onPanEnd: (_) => _dragStart.remove(node.id),
                    onTap: () => setState(() {
                      _selectedId = _selectedId == node.id ? null : node.id;
                    }),
                    child: HardwareNodeWidget(
                      node: node,
                      selected: _selectedId == node.id,
                    ),
                  ),
                )),
          ]),
        ),
      ),
      // Toolbar overlay
      Positioned(
        bottom: 12,
        right: 12,
        child: _Toolbar(
          onAutoLayout: () {
            ref
                .read(topologyProvider.notifier)
                .autoLayout(const Size(_canvasW, _canvasH));
          },
          onResetZoom: () => _transform.value = Matrix4.identity(),
        ),
      ),
    ]);
  }
}

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

  Widget _btn(IconData icon, String tooltip, VoidCallback onTap) =>
      Tooltip(
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
