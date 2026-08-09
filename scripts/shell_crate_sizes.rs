// Size harness for issue #171: what does a no_std line-editor crate cost in
// `.text` on riscv32imac, against the shell we already have?
//
// Every variant has the SAME payload: the same 28 top-level command words, the
// same per-command body, the same UART. What differs is only the line editor and
// dispatcher in front of them, so the delta over `base` is the crate.
#![no_std]
#![no_main]

use core::fmt::Write as _;

use riscv_rt::entry;

// ---------------------------------------------------------------------------
// The console: a 16550 at a fixed address, the way `src/uart.rs` drives one.
// ---------------------------------------------------------------------------

const UART_BASE: usize = 0xf000_0000;

pub struct Uart;

impl Uart {
    #[inline(never)]
    pub fn put(&mut self, byte: u8) {
        unsafe { core::ptr::write_volatile(UART_BASE as *mut u8, byte) }
    }

    #[inline(never)]
    pub fn get(&mut self) -> Option<u8> {
        let lsr = unsafe { core::ptr::read_volatile((UART_BASE + 5) as *const u8) };
        if lsr & 1 != 0 {
            Some(unsafe { core::ptr::read_volatile(UART_BASE as *const u8) })
        } else {
            None
        }
    }
}

impl core::fmt::Write for Uart {
    fn write_str(&mut self, s: &str) -> core::fmt::Result {
        for byte in s.bytes() {
            self.put(byte);
        }
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// The command table. The 28 top-level words of the real shell, with the
// descriptions from its HELP table so `.rodata` is comparable too.
// ---------------------------------------------------------------------------

pub const NAMES: [&str; 28] = [
    "bench", "board", "bram", "check", "cpu", "flash", "help", "hr", "hyperram", "i2c", "info",
    "irq", "led", "load", "log", "map", "phy", "pmod", "ports", "power", "reset", "rtic",
    "selftest", "sideband", "time", "typec", "usb", "vbus",
];

pub const SUMMARY: [&str; 28] = [
    "time bram, flash or hyperram",
    "every connector, rail and controller",
    "one word of block RAM",
    "arithmetic the compiler could have folded, at runtime",
    "cycles, instructions, busy fraction",
    "the first flash word, and the size",
    "this list",
    "hyperram: see `hr`",
    "one word over the staging port",
    "scan a bus behind the mux",
    "image, memory, boot, cpu, gateware",
    "interrupt counts, per source",
    "the six LEDs",
    "stage <hex> bytes of firmware, then boot it",
    "the deferred event log",
    "every peripheral window, from the generated map",
    "the USB PHYs",
    "connector pins: ball, resource, free or claimed",
    "which UARTs answer",
    "the four PAC1954 channels",
    "jump to the reset vector",
    "the dispatcher: model, task jitter, stalls",
    "run every self-check",
    "the sideband link",
    "uptime, from mtime",
    "the FUSB302B controllers",
    "the synthetic USB workload",
    "the VBUS distribution switches",
];

/// The body of every command, identical in every variant so that the only thing
/// being differenced is the editor in front of it.
#[inline(never)]
pub fn dispatch(uart: &mut Uart, index: usize, rest_len: usize) {
    let _ = writeln!(uart, "{} {} {}", NAMES[index], SUMMARY[index], rest_len);
}

#[inline(never)]
pub fn unknown(uart: &mut Uart) {
    let _ = uart.write_str("unknown command; try `help`\n");
}

#[panic_handler]
fn panic(_info: &core::panic::PanicInfo) -> ! {
    loop {}
}

// ===========================================================================
// base -- what the shell does today: 64-byte buffer, backspace, exact match.
// ===========================================================================

// A heap, priced: `embedded-alloc`'s allocator plus a `Vec`-backed line buffer
// and a `String` history, which is the shape every alloc-using line editor has.
#[cfg(feature = "v_alloc")]
mod heap {
    extern crate alloc;
    use alloc::string::String;
    use alloc::vec::Vec;
    use embedded_alloc::LlffHeap as Heap;

    #[global_allocator]
    static HEAP: Heap = Heap::empty();

    static mut ARENA: [u8; 4096] = [0u8; 4096];

    pub fn init() {
        unsafe {
            HEAP.init(core::ptr::addr_of_mut!(ARENA) as usize, 4096);
        }
    }

    #[inline(never)]
    pub fn exercise(byte: u8) -> usize {
        let mut line: Vec<u8> = Vec::with_capacity(64);
        line.push(byte);
        let mut history: Vec<String> = Vec::new();
        let mut owned = String::new();
        owned.push(byte as char);
        history.push(owned);
        line.len() + history.len() + history[0].len()
    }
}

#[cfg(not(any(
    feature = "v_embedded_cli",
    feature = "v_noline",
    feature = "v_menu",
    feature = "v_ushell",
    feature = "v_nutshell",
    feature = "v_prefix",
)))]
mod variant {
    use super::*;

    pub struct Shell {
        line: [u8; 64],
        len: usize,
    }

    impl Shell {
        pub const NEW: Shell = Shell {
            line: [0u8; 64],
            len: 0,
        };

        pub fn poll(&mut self, uart: &mut Uart) {
            let byte = match uart.get() {
                Some(byte) => byte,
                None => return,
            };
            match byte {
                b'\r' | b'\n' => {
                    let _ = uart.write_str("\n");
                    if self.len > 0 {
                        let len = self.len;
                        let mut line = [0u8; 64];
                        line[..len].copy_from_slice(&self.line[..len]);
                        self.len = 0;
                        run(uart, &line[..len]);
                    }
                    let _ = uart.write_str("> ");
                }
                0x08 | 0x7f => {
                    if self.len > 0 {
                        self.len -= 1;
                        let _ = uart.write_str("\x08 \x08");
                    }
                }
                0x20..=0x7e => {
                    if self.len < self.line.len() {
                        self.line[self.len] = byte;
                        self.len += 1;
                        uart.put(byte);
                    }
                }
                _ => {}
            }
        }
    }

    fn run(uart: &mut Uart, line: &[u8]) {
        let (cmd, rest) = match line.iter().position(|&b| b == b' ') {
            Some(i) => (&line[..i], &line[i + 1..]),
            None => (line, &line[..0]),
        };
        for (index, name) in NAMES.iter().enumerate() {
            if name.as_bytes() == cmd {
                dispatch(uart, index, rest.len());
                return;
            }
        }
        unknown(uart);
    }
}

// ===========================================================================
// prefix -- the same shell plus unique-prefix dispatch, TAB completion with
// ambiguity listing, and an 8-deep history ring. The DIY option, priced.
// ===========================================================================

#[cfg(feature = "v_prefix")]
mod variant {
    use super::*;

    const HISTORY: usize = 8;

    pub struct Shell {
        line: [u8; 64],
        len: usize,
        hist: [[u8; 64]; HISTORY],
        hist_len: [u8; HISTORY],
        hist_head: usize,
        hist_count: usize,
        hist_cursor: usize,
        escape: u8,
    }

    impl Shell {
        pub const NEW: Shell = Shell {
            line: [0u8; 64],
            len: 0,
            hist: [[0u8; 64]; HISTORY],
            hist_len: [0u8; HISTORY],
            hist_head: 0,
            hist_count: 0,
            hist_cursor: 0,
            escape: 0,
        };

        pub fn poll(&mut self, uart: &mut Uart) {
            let byte = match uart.get() {
                Some(byte) => byte,
                None => return,
            };

            // Arrow keys: ESC [ A / ESC [ B, decoded by a three-state counter
            // rather than a parser.
            match (self.escape, byte) {
                (0, 0x1b) => {
                    self.escape = 1;
                    return;
                }
                (1, b'[') => {
                    self.escape = 2;
                    return;
                }
                (2, b'A') => {
                    self.escape = 0;
                    self.recall(uart, true);
                    return;
                }
                (2, b'B') => {
                    self.escape = 0;
                    self.recall(uart, false);
                    return;
                }
                (1..=2, _) => {
                    self.escape = 0;
                    return;
                }
                _ => {}
            }

            match byte {
                b'\r' | b'\n' => {
                    let _ = uart.write_str("\n");
                    if self.len > 0 {
                        let len = self.len;
                        let mut line = [0u8; 64];
                        line[..len].copy_from_slice(&self.line[..len]);
                        self.remember(len);
                        self.len = 0;
                        run(uart, &line[..len]);
                    }
                    let _ = uart.write_str("> ");
                }
                b'\t' => self.complete(uart),
                0x08 | 0x7f => {
                    if self.len > 0 {
                        self.len -= 1;
                        let _ = uart.write_str("\x08 \x08");
                    }
                }
                0x20..=0x7e => {
                    if self.len < self.line.len() {
                        self.line[self.len] = byte;
                        self.len += 1;
                        uart.put(byte);
                    }
                }
                _ => {}
            }
        }

        fn remember(&mut self, len: usize) {
            let slot = self.hist_head;
            self.hist[slot][..len].copy_from_slice(&self.line[..len]);
            self.hist_len[slot] = len as u8;
            self.hist_head = (slot + 1) % HISTORY;
            if self.hist_count < HISTORY {
                self.hist_count += 1;
            }
            self.hist_cursor = 0;
        }

        fn recall(&mut self, uart: &mut Uart, back: bool) {
            if self.hist_count == 0 {
                return;
            }
            if back {
                if self.hist_cursor == self.hist_count {
                    return;
                }
                self.hist_cursor += 1;
            } else {
                if self.hist_cursor == 0 {
                    return;
                }
                self.hist_cursor -= 1;
            }
            // Erase what is on screen, then draw the recalled line.
            for _ in 0..self.len {
                let _ = uart.write_str("\x08 \x08");
            }
            self.len = 0;
            if self.hist_cursor > 0 {
                let slot = (self.hist_head + HISTORY - self.hist_cursor) % HISTORY;
                let len = self.hist_len[slot] as usize;
                for i in 0..len {
                    let byte = self.hist[slot][i];
                    self.line[i] = byte;
                    uart.put(byte);
                }
                self.len = len;
            }
        }

        /// TAB: complete to the longest common prefix, or list the candidates.
        fn complete(&mut self, uart: &mut Uart) {
            let word = &self.line[..self.len];
            if word.iter().any(|&b| b == b' ') {
                return;
            }
            let mut matches = 0;
            let mut first = 0;
            let mut common = 0usize;
            for (index, name) in NAMES.iter().enumerate() {
                if !name.as_bytes().starts_with(word) {
                    continue;
                }
                if matches == 0 {
                    first = index;
                    common = name.len();
                } else {
                    let a = NAMES[first].as_bytes();
                    let b = name.as_bytes();
                    let mut i = 0;
                    while i < common && i < b.len() && a[i] == b[i] {
                        i += 1;
                    }
                    common = i;
                }
                matches += 1;
            }
            if matches == 0 {
                return;
            }
            if matches > 1 {
                let _ = uart.write_str("\n");
                for name in NAMES.iter() {
                    if name.as_bytes().starts_with(word) {
                        let _ = uart.write_str("  ");
                        let _ = uart.write_str(name);
                    }
                }
                let _ = uart.write_str("\n> ");
                for i in 0..self.len {
                    uart.put(self.line[i]);
                }
            }
            let name = NAMES[first].as_bytes();
            for i in self.len..common {
                if self.len < self.line.len() {
                    self.line[self.len] = name[i];
                    self.len += 1;
                    uart.put(name[i]);
                }
            }
        }
    }

    /// Exact match first, then the unique prefix. Ambiguity is REPORTED.
    fn run(uart: &mut Uart, line: &[u8]) {
        let (cmd, rest) = match line.iter().position(|&b| b == b' ') {
            Some(i) => (&line[..i], &line[i + 1..]),
            None => (line, &line[..0]),
        };
        let mut matches = 0;
        let mut first = 0;
        for (index, name) in NAMES.iter().enumerate() {
            let bytes = name.as_bytes();
            if bytes == cmd {
                dispatch(uart, index, rest.len());
                return;
            }
            if bytes.starts_with(cmd) {
                if matches == 0 {
                    first = index;
                }
                matches += 1;
            }
        }
        match matches {
            0 => unknown(uart),
            1 => dispatch(uart, first, rest.len()),
            _ => {
                let _ = uart.write_str("ambiguous:");
                for name in NAMES.iter() {
                    if name.as_bytes().starts_with(cmd) {
                        let _ = uart.write_str(" ");
                        let _ = uart.write_str(name);
                    }
                }
                let _ = uart.write_str("\n");
            }
        }
    }
}

// ===========================================================================
// embedded-cli 0.2.1
// ===========================================================================

#[cfg(feature = "v_embedded_cli")]
mod variant {
    use super::*;
    use embedded_cli::autocomplete::{Autocompletion, Request};
    use embedded_cli::cli::{Cli, CliBuilder, CliHandle};
    use embedded_cli::command::RawCommand;
    use embedded_cli::service::{Autocomplete, Help, HelpError, ProcessError};
    use embedded_cli::writer::Writer;

    pub struct IoWrite(pub Uart);

    impl embedded_io::ErrorType for IoWrite {
        type Error = core::convert::Infallible;
    }

    impl embedded_io::Write for IoWrite {
        fn write(&mut self, buf: &[u8]) -> Result<usize, Self::Error> {
            for &byte in buf {
                self.0.put(byte);
            }
            Ok(buf.len())
        }
        fn flush(&mut self) -> Result<(), Self::Error> {
            Ok(())
        }
    }

    struct Commands;

    impl Autocomplete for Commands {
        fn autocomplete(request: Request<'_>, autocompletion: &mut Autocompletion<'_>) {
            match request {
                Request::CommandName(name) => {
                    for candidate in NAMES.iter() {
                        if candidate.starts_with(name) {
                            autocompletion.merge_autocompletion(&candidate[name.len()..]);
                        }
                    }
                }
                _ => {}
            }
        }
    }

    impl Help for Commands {
        fn command_count() -> usize {
            NAMES.len()
        }

        fn list_commands<W: embedded_io::Write<Error = E>, E: embedded_io::Error>(
            writer: &mut Writer<'_, W, E>,
        ) -> Result<(), E> {
            for (name, summary) in NAMES.iter().zip(SUMMARY.iter()) {
                writer.write_list_element(name, summary, 10)?;
            }
            Ok(())
        }

        fn command_help<
            W: embedded_io::Write<Error = E>,
            E: embedded_io::Error,
            F: FnMut(&mut Writer<'_, W, E>) -> Result<(), E>,
        >(
            _parent: &mut F,
            command: RawCommand<'_>,
            writer: &mut Writer<'_, W, E>,
        ) -> Result<(), HelpError<E>> {
            for (name, summary) in NAMES.iter().zip(SUMMARY.iter()) {
                if *name == command.name() {
                    writer.write_str(name)?;
                    writer.writeln_str(summary)?;
                    return Ok(());
                }
            }
            Err(HelpError::UnknownCommand)
        }
    }

    struct Processor;

    impl embedded_cli::service::CommandProcessor<IoWrite, core::convert::Infallible> for Processor {
        fn process<'a>(
            &mut self,
            _cli: &mut CliHandle<'_, IoWrite, core::convert::Infallible>,
            raw: RawCommand<'a>,
        ) -> Result<(), ProcessError<'a, core::convert::Infallible>> {
            let mut out = Uart;
            for (index, name) in NAMES.iter().enumerate() {
                if *name == raw.name() {
                    dispatch(&mut out, index, 0);
                    return Ok(());
                }
            }
            unknown(&mut out);
            Ok(())
        }
    }

    type TheCli = Cli<IoWrite, core::convert::Infallible, [u8; 64], [u8; 512]>;

    pub struct Shell {
        cli: Option<TheCli>,
    }

    impl Shell {
        pub const NEW: Shell = Shell { cli: None };

        pub fn poll(&mut self, uart: &mut Uart) {
            if self.cli.is_none() {
                self.cli = CliBuilder::default()
                    .writer(IoWrite(Uart))
                    .command_buffer([0u8; 64])
                    .history_buffer([0u8; 512])
                    .prompt("> ")
                    .build()
                    .ok();
            }
            let byte = match uart.get() {
                Some(byte) => byte,
                None => return,
            };
            if let Some(cli) = self.cli.as_mut() {
                let _ = cli.process_byte::<Commands, _>(byte, &mut Processor);
            }
        }
    }
}

// ===========================================================================
// noline 0.5.1 -- a line editor only; dispatch stays ours.
// ===========================================================================

#[cfg(feature = "v_noline")]
mod variant {
    use super::*;
    use noline::builder::EditorBuilder;

    pub struct Io(pub Uart);

    impl embedded_io::ErrorType for Io {
        type Error = core::convert::Infallible;
    }

    impl embedded_io::Write for Io {
        fn write(&mut self, buf: &[u8]) -> Result<usize, Self::Error> {
            for &byte in buf {
                self.0.put(byte);
            }
            Ok(buf.len())
        }
        fn flush(&mut self) -> Result<(), Self::Error> {
            Ok(())
        }
    }

    impl embedded_io::Read for Io {
        fn read(&mut self, buf: &mut [u8]) -> Result<usize, Self::Error> {
            loop {
                if let Some(byte) = self.0.get() {
                    buf[0] = byte;
                    return Ok(1);
                }
            }
        }
    }

    pub struct Shell;

    impl Shell {
        pub const NEW: Shell = Shell;

        pub fn poll(&mut self, uart: &mut Uart) {
            let _ = uart;
            let mut io = Io(Uart);
            let mut line = [0u8; 64];
            let mut hist = [0u8; 512];
            let mut editor = match EditorBuilder::from_slice(&mut line)
                .with_slice_history(&mut hist)
                .build_sync(&mut io)
            {
                Ok(editor) => editor,
                Err(_) => return,
            };
            loop {
                match editor.readline("> ", &mut io) {
                    Ok(line) => {
                        let bytes = line.as_bytes();
                        let (cmd, rest) = match bytes.iter().position(|&b| b == b' ') {
                            Some(i) => (&bytes[..i], &bytes[i + 1..]),
                            None => (bytes, &bytes[..0]),
                        };
                        let mut out = Uart;
                        let mut found = false;
                        for (index, name) in NAMES.iter().enumerate() {
                            if name.as_bytes() == cmd {
                                dispatch(&mut out, index, rest.len());
                                found = true;
                                break;
                            }
                        }
                        if !found {
                            unknown(&mut out);
                        }
                    }
                    Err(_) => return,
                }
            }
        }
    }
}

// ===========================================================================
// menu 0.6.1
// ===========================================================================

#[cfg(feature = "v_menu")]
mod variant {
    use super::*;
    use menu::{Item, ItemType, Menu, Parameter, Runner};

    struct Out;

    impl embedded_io::ErrorType for Out {
        type Error = core::convert::Infallible;
    }

    impl embedded_io::Write for Out {
        fn write(&mut self, buf: &[u8]) -> Result<usize, Self::Error> {
            for &byte in buf {
                Uart.put(byte);
            }
            Ok(buf.len())
        }
        fn flush(&mut self) -> Result<(), Self::Error> {
            Ok(())
        }
    }

    fn handler(_menu: &Menu<Out, ()>, item: &Item<Out, ()>, args: &[&str], _out: &mut Out, _ctx: &mut ()) {
        let mut uart = Uart;
        for (index, name) in NAMES.iter().enumerate() {
            if *name == item.command {
                dispatch(&mut uart, index, args.len());
                return;
            }
        }
        unknown(&mut uart);
    }

    macro_rules! item {
        ($i:expr) => {
            &Item {
                command: NAMES[$i],
                help: Some(SUMMARY[$i]),
                item_type: ItemType::Callback {
                    function: handler,
                    parameters: &[Parameter::Optional {
                        parameter_name: "arg",
                        help: None,
                    }],
                },
            }
        };
    }

    const ITEMS: &[&Item<Out, ()>] = &[
        item!(0), item!(1), item!(2), item!(3), item!(4), item!(5), item!(6),
        item!(7), item!(8), item!(9), item!(10), item!(11), item!(12), item!(13),
        item!(14), item!(15), item!(16), item!(17), item!(18), item!(19), item!(20),
        item!(21), item!(22), item!(23), item!(24), item!(25), item!(26), item!(27),
    ];

    const ROOT: Menu<Out, ()> = Menu {
        label: "root",
        items: ITEMS,
        entry: None,
        exit: None,
    };

    static mut BUFFER: [u8; 64] = [0u8; 64];

    pub struct Shell {
        runner: Option<Runner<'static, Out, (), [u8; 64]>>,
    }

    impl Shell {
        pub const NEW: Shell = Shell { runner: None };

        pub fn poll(&mut self, uart: &mut Uart) {
            if self.runner.is_none() {
                let buffer = unsafe { &mut *core::ptr::addr_of_mut!(BUFFER) };
                self.runner = Some(Runner::new(ROOT, buffer, Out, &mut ()));
            }
            let byte = match uart.get() {
                Some(byte) => byte,
                None => return,
            };
            if let Some(runner) = self.runner.as_mut() {
                runner.input_byte(byte, &mut ());
            }
        }
    }
}

// ===========================================================================
// ushell 0.4.0
// ===========================================================================

#[cfg(feature = "v_ushell")]
mod variant {
    use super::*;
    use ushell::{
        autocomplete::StaticAutocomplete, history::LRUHistory, Environment, Input, ShellError,
        SpinResult, UShell,
    };

    pub struct Serial;

    impl ushell::Read<u8> for Serial {
        type Error = ();
        fn read(&mut self) -> nb::Result<u8, ()> {
            match Uart.get() {
                Some(byte) => Ok(byte),
                None => Err(nb::Error::WouldBlock),
            }
        }
    }

    impl ushell::Write<u8> for Serial {
        type Error = ();
        fn write(&mut self, word: u8) -> nb::Result<(), ()> {
            Uart.put(word);
            Ok(())
        }
        fn flush(&mut self) -> nb::Result<(), ()> {
            Ok(())
        }
    }

    type TheShell = UShell<Serial, StaticAutocomplete<28>, LRUHistory<64, 8>, 64>;

    struct Env;

    impl Environment<Serial, StaticAutocomplete<28>, LRUHistory<64, 8>, (), 64> for Env {
        fn command(&mut self, _shell: &mut TheShell, cmd: &str, args: &str) -> SpinResult<Serial, ()> {
            let mut uart = Uart;
            for (index, name) in NAMES.iter().enumerate() {
                if *name == cmd {
                    dispatch(&mut uart, index, args.len());
                    return Ok(());
                }
            }
            unknown(&mut uart);
            Ok(())
        }

        fn control(&mut self, _shell: &mut TheShell, _code: u8) -> SpinResult<Serial, ()> {
            Ok(())
        }
    }

    pub struct Shell {
        inner: Option<TheShell>,
    }

    impl Shell {
        pub const NEW: Shell = Shell { inner: None };

        pub fn poll(&mut self, _uart: &mut Uart) {
            if self.inner.is_none() {
                self.inner = Some(UShell::new(
                    Serial,
                    StaticAutocomplete(NAMES),
                    LRUHistory::default(),
                ));
            }
            if let Some(shell) = self.inner.as_mut() {
                let _ = shell.spin(&mut Env);
            }
        }
    }

    // Keep `Input` and `ShellError` referenced so the enum bodies are linked.
    #[allow(dead_code)]
    fn _keep(i: Input<'_>) -> usize {
        match i {
            Input::Control(_) => 0,
            Input::Command(_) => 1,
        }
    }
    #[allow(dead_code)]
    fn _keep2(e: ShellError<Serial>) -> usize {
        match e {
            ShellError::WouldBlock => 0,
            _ => 1,
        }
    }
}

// ===========================================================================
// nut-shell 0.1.2
// ===========================================================================

#[cfg(feature = "v_nutshell")]
mod variant {
    use super::*;
    use nut_shell::{
        AccessLevel, CliError, CommandHandler, CommandKind, CommandMeta, DefaultConfig, Directory,
        Node, Response, Shell as NutShell,
    };

    #[derive(Debug, Copy, Clone, PartialEq, Eq, PartialOrd, Ord)]
    pub enum Level {
        User = 0,
    }

    impl AccessLevel for Level {
        fn from_str(_s: &str) -> Option<Self> {
            Some(Level::User)
        }
        fn as_str(&self) -> &'static str {
            "User"
        }
    }

    pub struct Io;

    impl nut_shell::CharIo for Io {
        type Error = ();
        fn get_char(&mut self) -> Result<Option<char>, ()> {
            Ok(Uart.get().map(|b| b as char))
        }
        fn put_char(&mut self, c: char) -> Result<(), ()> {
            Uart.put(c as u8);
            Ok(())
        }
    }

    struct Handler;

    impl CommandHandler<DefaultConfig> for Handler {
        fn execute_sync(&self, id: &str, args: &[&str]) -> Result<Response<DefaultConfig>, CliError> {
            let mut uart = Uart;
            for (index, name) in NAMES.iter().enumerate() {
                if *name == id {
                    dispatch(&mut uart, index, args.len());
                    return Ok(Response::success(""));
                }
            }
            Err(CliError::CommandNotFound)
        }
    }

    macro_rules! meta {
        ($i:expr) => {{
            const M: CommandMeta<Level> = CommandMeta {
                id: NAMES[$i],
                name: NAMES[$i],
                description: SUMMARY[$i],
                access_level: Level::User,
                kind: CommandKind::Sync,
                min_args: 0,
                max_args: 4,
            };
            Node::Command(&M)
        }};
    }

    static CHILDREN: &[Node<Level>] = &[
        meta!(0), meta!(1), meta!(2), meta!(3), meta!(4), meta!(5), meta!(6),
        meta!(7), meta!(8), meta!(9), meta!(10), meta!(11), meta!(12), meta!(13),
        meta!(14), meta!(15), meta!(16), meta!(17), meta!(18), meta!(19), meta!(20),
        meta!(21), meta!(22), meta!(23), meta!(24), meta!(25), meta!(26), meta!(27),
    ];

    static ROOT: Directory<Level> = Directory {
        name: "",
        children: CHILDREN,
        access_level: Level::User,
    };

    pub struct Shell {
        inner: Option<NutShell<'static, Level, Io, Handler, DefaultConfig>>,
    }

    impl Shell {
        pub const NEW: Shell = Shell { inner: None };

        pub fn poll(&mut self, uart: &mut Uart) {
            if self.inner.is_none() {
                let mut shell = NutShell::new(&ROOT, Handler, Io);
                let _ = shell.activate();
                self.inner = Some(shell);
            }
            let byte = match uart.get() {
                Some(byte) => byte,
                None => return,
            };
            if let Some(shell) = self.inner.as_mut() {
                let _ = shell.process_char(byte as char);
            }
        }
    }
}

// ===========================================================================

#[cfg(feature = "v_ratatui")]
mod cellprobe {
    extern crate alloc;
    use embedded_alloc::LlffHeap as Heap;
    #[global_allocator]
    static HEAP: Heap = Heap::empty();
    /// `size_of::<Cell>()` on riscv32imac, asserted so a version bump that changes
    /// it fails the build rather than quietly invalidating the RAM figure in #171.
    const _CELL_PROBE: [(); 28] = [(); core::mem::size_of::<ratatui_core::buffer::Cell>()];

    static mut ARENA: [u8; 4096] = [0u8; 4096];

    /// The smallest thing ratatui can be asked to do: one 80x24 buffer, one
    /// styled string written into it, one layout split.
    #[inline(never)]
    pub fn render() -> u32 {
        use ratatui_core::buffer::Buffer;
        use ratatui_core::layout::{Constraint, Layout, Rect};
        use ratatui_core::style::{Style, Stylize};
        unsafe { HEAP.init(core::ptr::addr_of_mut!(ARENA) as usize, 4096) };
        let area = Rect::new(0, 0, 80, 24);
        let mut buf = Buffer::empty(area);
        let rows = Layout::vertical([Constraint::Length(8), Constraint::Min(1)]).split(area);
        buf.set_string(0, 0, "power", Style::new().green());
        buf.set_string(0, 1, "typec", Style::new().red().bold());
        rows[0].height as u32 + buf.area().width as u32
    }
}

const MAX_CONSOLES: usize = 4;

#[entry]
fn main() -> ! {
    let mut shells = [variant::Shell::NEW, variant::Shell::NEW];
    let mut uart = Uart;
    let _ = MAX_CONSOLES;
    #[cfg(feature = "v_ratatui")]
    {
        let n = cellprobe::render();
        let _ = writeln!(uart, "ratatui {}", n);
    }
    #[cfg(feature = "v_alloc")]
    {
        heap::init();
        let n = heap::exercise(0x41);
        let _ = writeln!(uart, "heap {}", n);
    }
    loop {
        for shell in shells.iter_mut() {
            shell.poll(&mut uart);
        }
    }
}
