import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../providers/transport_provider.dart';
import '../services/transport/mdns_discovery.dart';
import '../theme.dart' as theme;

class ConnectScreen extends ConsumerStatefulWidget {
  const ConnectScreen({super.key});

  @override
  ConsumerState<ConnectScreen> createState() => _ConnectScreenState();
}

class _ConnectScreenState extends ConsumerState<ConnectScreen> {
  final _hostCtrl = TextEditingController(text: '');
  final _portCtrl = TextEditingController(text: '7777');
  bool _scanning = false;
  bool _connecting = false;
  List<DiscoveredHost> _discovered = [];
  String? _error;

  @override
  void dispose() {
    _hostCtrl.dispose();
    _portCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: theme.bgCanvas,
      body: Center(
        child: SizedBox(
          width: 420,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              _header(),
              const SizedBox(height: 24),
              _section('WiFi / WebSocket'),
              const SizedBox(height: 12),
              _wifiForm(),
              const SizedBox(height: 16),
              _discoverRow(),
              if (_discovered.isNotEmpty) ...[
                const SizedBox(height: 12),
                ..._discovered.map(_discoveredTile),
              ],
              if (_error != null) ...[
                const SizedBox(height: 12),
                Text(_error!, style: const TextStyle(color: Color(0xFFF85149), fontSize: 12)),
              ],
              const SizedBox(height: 32),
              _section('BLE'),
              const SizedBox(height: 8),
              _bleTile(),
            ],
          ),
        ),
      ),
    );
  }

  Widget _header() => Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Container(width: 8, height: 8,
              decoration: const BoxDecoration(color: Color(0xFFF85149), shape: BoxShape.circle)),
          const SizedBox(width: 8),
          const Text('Cynthion Monitor',
              style: TextStyle(color: theme.textPrimary, fontSize: 16, fontWeight: FontWeight.w600)),
        ]),
        const SizedBox(height: 4),
        const Text('Connect to apollod', style: TextStyle(color: theme.textMuted, fontSize: 12)),
      ]);

  Widget _section(String label) => Text(label.toUpperCase(),
      style: const TextStyle(color: theme.textMuted, fontSize: 10, letterSpacing: 1.2));

  Widget _wifiForm() => Row(children: [
        Expanded(
          flex: 3,
          child: _field(_hostCtrl, 'host or IP', hint: '192.168.1.x'),
        ),
        const SizedBox(width: 8),
        SizedBox(width: 80, child: _field(_portCtrl, 'port')),
        const SizedBox(width: 8),
        _btn('Connect', _connecting ? null : _connectWifi),
      ]);

  Widget _field(TextEditingController ctrl, String label, {String? hint}) =>
      TextField(
        controller: ctrl,
        style: const TextStyle(color: theme.textPrimary, fontSize: 13, fontFamily: 'monospace'),
        decoration: InputDecoration(
          labelText: label,
          hintText: hint,
          hintStyle: TextStyle(color: theme.textMuted.withValues(alpha: 0.4)),
          labelStyle: const TextStyle(color: theme.textMuted, fontSize: 11),
          enabledBorder: const OutlineInputBorder(
              borderSide: BorderSide(color: theme.borderColor)),
          focusedBorder: const OutlineInputBorder(
              borderSide: BorderSide(color: theme.colHost)),
          contentPadding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
        ),
      );

  Widget _discoverRow() => Row(children: [
        _btn(_scanning ? 'Scanning…' : 'Scan LAN', _scanning ? null : _scanMdns,
            icon: Icons.wifi_find),
        const SizedBox(width: 8),
        const Text('auto-detect apollod on local network',
            style: TextStyle(color: theme.textMuted, fontSize: 11)),
      ]);

  Widget _discoveredTile(DiscoveredHost h) => Padding(
        padding: const EdgeInsets.only(top: 6),
        child: InkWell(
          onTap: () {
            _hostCtrl.text = h.host;
            _portCtrl.text = h.port.toString();
          },
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 7),
            decoration: BoxDecoration(
              color: theme.bgCard,
              border: Border.all(color: theme.borderColor),
              borderRadius: BorderRadius.circular(6),
            ),
            child: Row(children: [
              const Icon(Icons.computer, size: 14, color: theme.colApollo),
              const SizedBox(width: 8),
              Text('${h.name}  ${h.host}:${h.port}',
                  style: const TextStyle(color: theme.textPrimary, fontSize: 12, fontFamily: 'monospace')),
            ]),
          ),
        ),
      );

  Widget _bleTile() => Container(
        padding: const EdgeInsets.all(10),
        decoration: BoxDecoration(
          color: theme.bgCard,
          border: Border.all(color: theme.borderColor),
          borderRadius: BorderRadius.circular(6),
        ),
        child: Row(children: [
          const Icon(Icons.bluetooth, size: 14, color: theme.textMuted),
          const SizedBox(width: 8),
          const Expanded(
            child: Text('BLE scan — tap to open (requires apollod --ble on host)',
                style: TextStyle(color: theme.textMuted, fontSize: 11)),
          ),
          _btn('Scan BLE', null, icon: Icons.bluetooth_searching),
        ]),
      );

  Widget _btn(String label, VoidCallback? onTap, {IconData? icon}) => TextButton(
        onPressed: onTap,
        style: TextButton.styleFrom(
          backgroundColor: onTap != null ? theme.bgCard : Colors.transparent,
          side: BorderSide(color: onTap != null ? theme.borderColor : Colors.transparent),
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(6)),
        ),
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          if (icon != null) ...[
            Icon(icon, size: 13, color: theme.textMuted),
            const SizedBox(width: 5),
          ],
          Text(label,
              style: TextStyle(
                  color: onTap != null ? theme.textPrimary : theme.textMuted, fontSize: 12)),
        ]),
      );

  Future<void> _connectWifi() async {
    setState(() { _connecting = true; _error = null; });
    try {
      final host = _hostCtrl.text.trim();
      final port = int.tryParse(_portCtrl.text.trim()) ?? 7777;
      await ref.read(transportProvider.notifier).connectWifi(host, port);
      if (mounted) Navigator.of(context).pop();
    } catch (e) {
      setState(() => _error = 'Connection failed: $e');
    } finally {
      if (mounted) setState(() => _connecting = false);
    }
  }

  Future<void> _scanMdns() async {
    setState(() { _scanning = true; _discovered = []; });
    try {
      _discovered = await discoverApolloD();
    } finally {
      if (mounted) setState(() => _scanning = false);
    }
  }
}
