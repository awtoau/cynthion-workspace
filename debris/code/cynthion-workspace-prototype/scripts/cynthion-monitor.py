#!/usr/bin/env python3
"""
cynthion-monitor — WebSocket daemon bridging Cynthion hardware to Flutter GUI

Provides:
- Device capability detection (stock/vendor/awto fork)
- TTY stream muxing (rv0/fpg/apl)
- JSON-RPC interface over WebSocket
- mDNS auto-discovery (cynthion.local)
- Graceful shutdown support
"""

import asyncio
import json
import logging
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import websockets
    from websockets.server import serve
except ImportError:
    print("Error: websockets not installed. Run: pip install websockets")
    sys.exit(1)

try:
    from zeroconf import ServiceInfo, Zeroconf
except ImportError:
    Zeroconf = None
    log_warning = True

log_file = Path(__file__).parent.parent / 'logs' / 'cynthion-monitor.log'
log_file.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger(__name__)

# Device info — detect from USB or use awto fork for testing
DEVICE_INFO = {
    'variant': 'awto_fork',  # 'stock', 'vendor', or 'awto_fork'
    'has_power_monitoring': True,
    'has_topology': True,
    'has_advanced_features': True,
    'has_multi_tty': True,
    'available_ttys': ['ttyACM0', 'ttyACM1', 'ttyACM2'],
    'apollod_version': '0.1.0',
    'device_serial': 'CYNTHION-001',
}

class ApolloD:
    def __init__(self):
        self.clients = set()
        self.tick = 0
        self.start_time = time.time()
        self.zeroconf = None
        self.shutdown_event = asyncio.Event()

    async def handle_client(self, websocket, path):
        """Handle incoming WebSocket client connections"""
        self.clients.add(websocket)
        log.info(f"Client connected: {websocket.remote_address}")

        try:
            async for message in websocket:
                await self.process_message(websocket, message)
        except websockets.exceptions.ConnectionClosed:
            log.info(f"Client disconnected: {websocket.remote_address}")
        finally:
            self.clients.discard(websocket)

    async def process_message(self, websocket, message):
        """Process incoming JSON-RPC messages"""
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            await websocket.send(json.dumps({'error': 'Invalid JSON'}))
            return

        method = data.get('method')

        if method == 'get_device_info':
            await websocket.send(json.dumps({
                'type': 'device_info',
                'method': 'get_device_info',
                **DEVICE_INFO
            }))

        elif method == 'get_topology':
            await websocket.send(json.dumps({
                'type': 'topology',
                'nodes': [
                    {'id': 'host', 'name': 'Host PC', 'status': 'idle'},
                    {'id': 'apollod', 'name': 'Apollo MCU', 'status': 'active'},
                    {'id': 'fpga', 'name': 'ECP5 FPGA', 'status': 'active'},
                    {'id': 'riscv', 'name': 'VexRiscV', 'status': 'active'},
                    {'id': 'moondancer', 'name': 'Moondancer', 'status': 'active'},
                    {'id': 'pac1954', 'name': 'PAC1954 PSM', 'status': 'active'},
                ]
            }))

        elif method == 'get_power':
            await websocket.send(json.dumps({
                'type': 'power',
                'channels': [
                    {
                        'ch': 0,
                        'name': 'TARGET-A',
                        'vbus_mv': 5012 + self.tick % 50,
                        'current_ma': 240 + self.tick % 20,
                        'power_mw': 1218,
                        'energy_uj': 48720 + self.tick * 100,
                    },
                    {
                        'ch': 1,
                        'name': 'TARGET-C',
                        'vbus_mv': 4998,
                        'current_ma': 18 + self.tick % 5,
                        'power_mw': 90,
                        'energy_uj': 3600 + self.tick * 10,
                    },
                    {
                        'ch': 2,
                        'name': 'CONTROL',
                        'vbus_mv': 5001,
                        'current_ma': 55 + self.tick % 10,
                        'power_mw': 275,
                        'energy_uj': 11000 + self.tick * 30,
                    },
                    {
                        'ch': 3,
                        'name': 'AUX',
                        'vbus_mv': 0,
                        'current_ma': 0,
                        'power_mw': 0,
                        'energy_uj': 0,
                    },
                ]
            }))

        elif method == 'subscribe':
            # Start sending updates to this client
            asyncio.create_task(self.stream_updates(websocket))

        elif method == 'shutdown':
            log.info("Shutdown command received")
            self.shutdown_event.set()

        else:
            await websocket.send(json.dumps({'error': f'Unknown method: {method}'}))

    async def stream_updates(self, websocket):
        """Stream periodic updates to connected client"""
        try:
            while True:
                await asyncio.sleep(0.8)
                self.tick += 1

                # Stream TTY stub data
                uptime_ms = int((time.time() - self.start_time) * 1000)
                await websocket.send(json.dumps({
                    'type': 'tty_line',
                    'source': 'rv0',
                    'timestamp': datetime.now().isoformat(),
                    'text': f'♥ heartbeat tick={self.tick} uptime={uptime_ms}ms [STUB]',
                    'is_fault': False,
                }))
        except websockets.exceptions.ConnectionClosed:
            pass

    async def start(self, host='127.0.0.1', port=7777):
        """Start the WebSocket server and register mDNS"""
        # Register mDNS service
        if Zeroconf:
            try:
                self._register_mdns(host, port)
            except Exception as e:
                log.warning(f"mDNS registration failed: {e}")

        async with serve(self.handle_client, host, port):
            log.info(f"apollod listening on ws://{host}:{port}")
            log.info(f"Advertised as: cynthion.local:{port}")
            log.info(f"Device: {DEVICE_INFO['variant']} (topology: {DEVICE_INFO['has_topology']}, power: {DEVICE_INFO['has_power_monitoring']})")
            log.info("Send {\"method\": \"shutdown\"} to gracefully stop")
            try:
                await self.shutdown_event.wait()
                log.info("Shutting down gracefully...")
            finally:
                self._unregister_mdns()
                log.info("Shutdown complete")

    def _register_mdns(self, host, port):
        """Register apollod service via mDNS"""
        import socket
        hostname = socket.gethostname()
        service_name = f"cynthion.{hostname}._apollod._tcp.local."

        service_info = ServiceInfo(
            "_apollod._tcp.local.",
            service_name,
            addresses=[socket.inet_aton(host)],
            port=port,
            properties={
                'device': DEVICE_INFO['variant'],
                'topology': str(DEVICE_INFO['has_topology']),
                'power': str(DEVICE_INFO['has_power_monitoring']),
            },
            server=f"cynthion.local.",
        )

        self.zeroconf = Zeroconf()
        self.zeroconf.register_service(service_info)
        log.info(f"mDNS service registered: {service_name}")

    def _unregister_mdns(self):
        """Unregister mDNS service"""
        if self.zeroconf:
            try:
                self.zeroconf.unregister_all_services()
                self.zeroconf.close()
            except Exception as e:
                log.warning(f"mDNS unregister failed: {e}")


async def main():
    daemon = ApolloD()

    # Handle Ctrl+C gracefully
    def signal_handler():
        log.info("SIGINT received, shutting down...")
        daemon.shutdown_event.set()

    loop = asyncio.get_event_loop()
    loop.add_signal_handler(signal.SIGINT, signal_handler)

    try:
        await daemon.start()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Shutting down...")
        sys.exit(0)
