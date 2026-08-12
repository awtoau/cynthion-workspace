//! One console's line editor, its banner, and the round-robin over them.
//!
//! The last of the shell that was still in `main.rs` (#296). `main` now boots
//! the board and hands the consoles to this; it holds no shell state at all.
//!
//! Per-console, not global: `spoken` latching on one port must not silence the
//! banner on another, and two half-typed lines must not share a buffer.

use core::fmt::Write;

use crate::uart::Uart;
use crate::shell::editor::{editor, Commands, Dispatch, Editor, CANDIDATES};
use crate::{irq, metrics, target, Devices, MAX_CONSOLES};

/// One console's line editor and its banner state.
///
/// Per-console rather than global: `spoken` latching on one port must not silence the
/// banner on another, and two half-typed command lines must not share a buffer.
/// `pub` for the reason [`Devices`] is: RTIC's `#[local]` resources land in
/// generated signatures too.
pub struct Shell {
    /// The WHOLE line being typed, shadowed.
    ///
    /// `Cli` owns the line and exposes no accessor, and the crate's
    /// `Autocomplete` hook has no writer -- so a TAB that wants to list, or to
    /// complete anything past the first word, needs the line tracked here.
    ///
    /// It holds the whole line, not the first word: cleared on every space,
    /// `i2c st` + TAB offers `selftest` and `sideband`, because the shadow says
    /// `st` and nothing records that a command name is not what is being typed.
    ///
    /// It can drift: recalling a line from history replaces the input without a
    /// printable byte passing through here. Cleared on anything that could
    /// desynchronise it, so a stale list is never shown -- TAB after a recall
    /// lists nothing rather than something wrong.
    line: [u8; LINE],
    line_len: usize,
    /// Where the cursor sits in `line`. Every synthesised key is bounded by it.
    cursor: usize,
    /// ANSI decoder state: 0 none, 1 saw ESC, 2 inside CSI.
    esc: u8,
    /// First CSI parameter byte, which is all `ESC[3~` and friends need.
    param: u8,
    /// Anything entered since the last Enter.
    typed: bool,
    /// The line editor, built on first keypress.
    ///
    /// `Option<Option<..>>`: the outer is "not built yet", the inner is "could
    /// not be built". `Shell::NEW` is a `const` used to fill an array, and a
    /// `Cli` needs a writer and two buffers, so it cannot be made in const
    /// context.
    editor: Option<Option<Editor>>,
    /// Set by the first keypress, which is what the banner waits for. From then
    /// on the prompt is on screen and reprinting it would fight the line being
    /// edited.
    spoken: bool,
}

/// The loop body's console half: one byte from each shell, round-robin.
///
/// Fair by construction and with no arbitration to get wrong: a console that is
/// being pasted into cannot starve the others, because it still only gets one
/// byte per turn.
///
/// Bytes come from the interrupt handler's rings, not from LSR, so the byte is
/// already collected before this asks for it -- a console busy printing cannot
/// miss one. What the caller still decides is how much of one console's input is
/// handled before the other's, which is a fairness property worth keeping.
pub(crate) fn consoles(shells: &mut [Shell; MAX_CONSOLES], devices: &mut Devices) {
    for (index, &base) in target::UART_BASES.iter().enumerate() {
        let mut uart = Uart::new(base);
        shells[index].poll(index, &mut uart, devices);
    }
}

pub(crate) fn banner(uart: &mut Uart) {
    let _ = write!(uart, "\n");
    crate::log!(uart, "Cynthion RISC-V SoC - Rust firmware");
    crate::log!(uart, "type `help` or `?` for commands");
}

/// What `led`, `i2c` and `sideband` say when there is no board under them.
///
/// The QEMU build has `target::BOARD == None`. Reporting that is better than
/// hiding the commands: `scripts/soc_test.py` then still checks that they are
/// registered and spelled the same as the help text, and a person who typed one
/// on the wrong target gets told which target they are on rather than
/// `unknown command`. See the comment on `target::BOARD`.
pub(crate) fn board_absent(uart: &mut Uart) {
    let _ = writeln!(uart, "no board peripherals on this target");
}

impl Shell {
    pub(crate) const NEW: Shell = Shell {
        line: [0u8; LINE],
        line_len: 0,
        cursor: 0,
        esc: 0,
        param: 0,
        typed: false,
        editor: None,
        spoken: false,
    };

    /// Handle at most one byte from `uart`. Returns immediately if none arrived.
    ///
    /// `index` selects this console's receive ring in `src/irq.rs`, and is also
    /// what `load` needs to know which port a transfer is arriving on. It is the
    /// index into `target::UART_BASES`, so it is the same number everywhere.
    pub(crate) fn poll(&mut self, index: usize, uart: &mut Uart, devices: &mut Devices) {
        // From the ring the interrupt handler fills, not from LSR. `uart` is still needed
        // for everything this function ECHOES; only the receive direction moved.
        let byte = match irq::pop(index) {
            Some(byte) => byte,
            None => return,
        };

        // THE BANNER IS ANSWERED, NEVER OFFERED. It waits here until the first
        // keypress and prints once.
        //
        // Reprinting on an idle console transmits with nobody there, and
        // console 1's TX is shared with JTAG TMS, where an unbidden
        // transmission is bus contention. Nothing here is unbidden, so every
        // console banners on its own first keypress.
        if !self.spoken {
            self.spoken = true;
            // Two lines printed is work, even though the byte that asked for
            // them was one keystroke. See `src/metrics.rs`.
            metrics::busy();
            banner(uart);
            // AND THE BOOT REPORT, which was transmitted before any tty was
            // bound and to console 0 only. Retained rather than reprinted --
            // each line carries the time it was pushed, not the time it is
            // being read. See `src/init.rs`.
            crate::init::replay(uart);
        }

        // DEL IS A BACKSPACE HERE. The crate matches `0x08` only, and `0x7f` is
        // >= 0x20 so it falls through to "printable" and gets INSERTED -- typing
        // backspace put a DEL character in the command line.
        //
        // Which byte a terminal sends is the terminal's choice, not the user's:
        // xterm and most Linux terminals send `0x7f`, a VT100 and this project's
        // own `scripts/tio_user.py` pass through whatever the tty gives them.
        // Measured on the board: `0x08` produced `ESC[D ESC[P`, `0x7f` echoed
        // itself and left the character standing.
        //
        // Translated before `process_byte` rather than patched in the crate, so
        // the crate stays a dependency rather than a fork.
        let byte = if byte == 0x7f { 0x08 } else { byte };

        // BEFORE the editor is borrowed: `decode` takes `&mut self`, and the
        // borrow of `self.editor` below is live for the rest of the function.
        // Field accesses stay disjoint; a method call does not.
        let key = self.decode(byte);

        // The line editor owns everything from here: echo, backspace, TAB
        // completion over `HELP`, and the history ring on the arrow keys.
        // A completed line reaches `shell::run` through `Dispatch` -- the crate
        // edits, this firmware still dispatches (#171, #303).
        let editor = match self.editor.get_or_insert_with(|| editor(crate::primary_for(index))) {
            Some(editor) => editor,
            // A CLI that could not be built means its buffers could not be
            // written to the console. Nothing useful is left to say on it.
            None => return,
        };
        // ENTER ON AN EMPTY LINE PRINTS THE LISTING.
        //
        // The crate does not call the processor for an empty line, so a bare
        // Enter reprinted the prompt and said nothing -- which is the one moment
        // a person is most likely to be asking what there is. `typed` tracks
        // whether anything was entered since the last Enter, because the word
        // shadow clears on a space and cannot answer this.
        // Restamp before the crate prints the next prompt.
        if byte == b'\r' || byte == b'\n' {
            refresh_prompt(index, editor);
        }
        if (byte == b'\r' || byte == b'\n') && !self.typed {
            let _ = editor.write(|writer| {
                use crate::shell::editor::Commands;
                use embedded_cli::service::Help;
                Commands::list_commands(writer)
            });
        }
        self.typed = match byte {
            b'\r' | b'\n' => false,
            0x20..=0x7e => true,
            _ => self.typed,
        };

        // ESCAPE SEQUENCES ARE NOT TEXT. Treated as text, `0x1b` clears the
        // shadow and then `[` and `C` push as printable, so a right-arrow
        // leaves `[C` in the completion buffer -- which looks harmless only
        // because no command starts with `[`.
        //
        // The state machine also gives the sequences a name, which is what
        // [`Self::key`] needs to add the keys the crate has no `ControlInput`
        // for.
        // Track the cursor as well as the text. Everything below is built out
        // of the crate's three primitives -- Backward, Forward, Backspace --
        // and each needs to know how far it can go before it starts eating the
        // wrong characters.
        // Captured before `Key::Enter` clears it below.
        let before_enter = self.line_len;
        match key {
            Key::Text(byte) => {
                if self.line_len < self.line.len() {
                    self.line.copy_within(self.cursor..self.line_len, self.cursor + 1);
                    self.line[self.cursor] = byte;
                    self.line_len += 1;
                    self.cursor += 1;
                }
            }
            Key::Backspace => {
                if self.cursor > 0 {
                    self.line.copy_within(self.cursor..self.line_len, self.cursor - 1);
                    self.line_len -= 1;
                    self.cursor -= 1;
                }
            }
            Key::Left => self.cursor = self.cursor.saturating_sub(1),
            Key::Right => {
                if self.cursor < self.line_len {
                    self.cursor += 1;
                }
            }
            // A recall replaces the line without a byte passing through here,
            // so the shadow cannot be kept true. Cleared rather than left
            // stale: TAB then offers nothing instead of something wrong.
            Key::Enter | Key::Abort | Key::History => {
                self.line_len = 0;
                self.cursor = 0;
            }
            _ => {}
        }

        // The line AS TYPED, before the crate tokenises it. `-250` is three
        // `Err(NonAsciiShortOption)`s to `ArgList`, and the sign was lost with
        // them (#347). Empty on a history recall, which never passed through
        // here -- `Dispatch` says so rather than dropping the argument.
        let mut typed = [0u8; LINE];
        let typed_len = if matches!(key, Key::Enter) {
            let n = before_enter.min(typed.len());
            typed[..n].copy_from_slice(&self.line[..n]);
            n
        } else {
            0
        };

        let mut dispatch = Dispatch { index, devices, typed: &typed[..typed_len] };
        let _ = editor.process_byte::<Commands, _>(byte, &mut dispatch);

        // KEYS THE CRATE HAS NO INPUT FOR, spelled with the ones it does.
        //
        // `embedded-cli`'s `process_csi` matches only A/B/C/D, so Delete, Home
        // and End arrive as nothing at all, and the readline control characters
        // are below 0x20 and fall through its `_ => None`. Rather than fork the
        // crate, each is expanded here into a run of Forward, Backward and
        // Backspace and fed back through `process_byte` -- the crate's own
        // input path, so its buffer, cursor and echo stay its business.
        //
        // Every count is bounded by the tracked cursor, which is what stops a
        // Delete at end-of-line becoming a Backspace over the previous
        // character -- the failure a naive Forward-then-Backspace has.
        //
        // EACH ARM UPDATES THE SHADOW AFTER ITS REPLAY. The replay moves the
        // crate and nothing else; leaving the shadow behind made `Home` then
        // `End` a no-op, because `End` computed its distance from a cursor
        // still sitting where `Home` had left it. Counts are read before the
        // update and the update is written after, in that order.
        match key {
            Key::Delete => {
                if self.cursor < self.line_len {
                    self.replay(index, devices, RIGHT, 1);
                    self.replay(index, devices, &[0x08], 1);
                    self.line.copy_within(self.cursor + 1..self.line_len, self.cursor);
                    self.line_len -= 1;
                }
            }
            Key::Home => {
                let n = self.cursor;
                self.replay(index, devices, LEFT, n);
                self.cursor = 0;
            }
            Key::End => {
                let n = self.line_len - self.cursor;
                self.replay(index, devices, RIGHT, n);
                self.cursor = self.line_len;
            }
            // To end of line: walk right, then rub out what was walked over.
            Key::KillToEnd => {
                let n = self.line_len - self.cursor;
                self.replay(index, devices, RIGHT, n);
                self.replay(index, devices, &[0x08], n);
                self.line_len = self.cursor;
            }
            Key::KillToStart => {
                let n = self.cursor;
                self.replay(index, devices, &[0x08], n);
                self.line.copy_within(n..self.line_len, 0);
                self.line_len -= n;
                self.cursor = 0;
            }
            // One word back: the run of spaces under the cursor, then the run
            // of non-spaces before it. Same rule as readline's `unix-word-rubout`.
            Key::KillWord => {
                let mut n = 0;
                while self.cursor > n && self.line[self.cursor - n - 1] == b' ' {
                    n += 1;
                }
                while self.cursor > n && self.line[self.cursor - n - 1] != b' ' {
                    n += 1;
                }
                self.replay(index, devices, &[0x08], n);
                self.line.copy_within(self.cursor..self.line_len, self.cursor - n);
                self.line_len -= n;
                self.cursor -= n;
            }
            _ => {}
        }

        if key == Key::Tab {
            self.tab(index, devices);
        }
    }

    /// One byte in, one key out, tracking ANSI escape sequences across calls.
    ///
    /// A byte at a time because that is how they arrive -- one per poll, from
    /// the interrupt handler's ring -- so the sequence has to be remembered
    /// between calls rather than parsed from a buffer.
    ///
    /// CSI is `ESC [`, then parameter bytes `0x30..=0x3f`, then a final byte
    /// `0x40..=0x7e`. Only the parameter's first digit is kept: every sequence
    /// this cares about is one digit (`3~` delete, `1~`/`7~` home, `4~`/`8~`
    /// end), and keeping more would need a buffer for no gain.
    fn decode(&mut self, byte: u8) -> Key {
        match self.esc {
            // Not in a sequence.
            0 => match byte {
                0x1b => {
                    self.esc = 1;
                    Key::None
                }
                0x01 => Key::Home,        // Ctrl-A
                0x03 => Key::Abort,       // Ctrl-C
                0x05 => Key::End,         // Ctrl-E
                0x08 => Key::Backspace,
                0x09 => Key::Tab,
                0x0b => Key::KillToEnd,   // Ctrl-K
                0x0d | 0x0a => Key::Enter,
                0x15 => Key::KillToStart, // Ctrl-U
                0x17 => Key::KillWord,    // Ctrl-W
                0x20..=0x7e => Key::Text(byte),
                _ => Key::None,
            },
            // Saw ESC. Anything but `[` is a lone escape or something this does
            // not speak; drop back rather than swallow the next key.
            1 => {
                self.esc = if byte == b'[' { 2 } else { 0 };
                self.param = 0;
                Key::None
            }
            // Inside CSI.
            _ => {
                if (0x30..=0x3f).contains(&byte) {
                    if self.param == 0 {
                        self.param = byte;
                    }
                    return Key::None;
                }
                self.esc = 0;
                match (byte, self.param) {
                    (b'A', _) | (b'B', _) => Key::History,
                    (b'C', _) => Key::Right,
                    (b'D', _) => Key::Left,
                    (b'H', _) | (b'~', b'1') | (b'~', b'7') => Key::Home,
                    (b'F', _) | (b'~', b'4') | (b'~', b'8') => Key::End,
                    (b'~', b'3') => Key::Delete,
                    _ => Key::None,
                }
            }
        }
    }

    /// Feed `sequence` to the crate `count` times.
    ///
    /// The shadow is NOT updated here: the caller has already moved it to where
    /// the replay will leave the crate, and doing it twice would double every
    /// move.
    fn replay(&mut self, index: usize, devices: &mut Devices, sequence: &[u8], count: usize) {
        let Some(Some(editor)) = self.editor.as_mut() else {
            return;
        };
        for _ in 0..count {
            for &byte in sequence {
                let mut dispatch = Dispatch { index, devices, typed: &[] };
                let _ = editor.process_byte::<Commands, _>(byte, &mut dispatch);
            }
        }
    }

    /// One TAB, at whatever depth the line has reached.
    ///
    /// Split out because it needs `self.line` and `self.editor` at once, and
    /// because the two halves are genuinely different jobs: INSERT what every
    /// candidate agrees on, then LIST what is left to choose between.
    ///
    /// The crate does the inserting for the first word and nothing after it --
    /// `Request::from_input` returns `None` once the line contains a space. Past
    /// that point the completion is fed back in as keystrokes through
    /// `process_byte`, which is the crate's own input path, so the echo, the
    /// cursor and the command buffer all stay its business rather than becoming
    /// a second implementation of them here.
    fn tab(&mut self, index: usize, devices: &mut Devices) {
        let Some(Some(editor)) = self.editor.as_mut() else {
            return;
        };
        let line = core::str::from_utf8(&self.line[..self.line_len]).unwrap_or("");

        let mut names = [""; CANDIDATES];
        let count = crate::shell::complete(line, &mut names);
        let shown = count.min(names.len());

        // How much of the current word is already typed, so only the remainder
        // is offered.
        let partial = if line.ends_with(' ') {
            ""
        } else {
            line.rsplit(' ').next().unwrap_or("")
        };

        let common = crate::shell::shared_prefix(&names[..shown]);
        let mut insert = [0u8; 32];
        let mut insert_len = 0;
        if common.len() > partial.len() {
            let extra = common[partial.len()..].as_bytes();
            insert_len = extra.len().min(insert.len());
            insert[..insert_len].copy_from_slice(&extra[..insert_len]);
        }

        // The first word is the crate's; it has already inserted the same bytes,
        // so only the shadow needs catching up or the next TAB works from a
        // stale line. Anything deeper is ours to type in.
        let ours = line.contains(' ');
        for &byte in &insert[..insert_len] {
            if ours {
                let mut dispatch = Dispatch { index, devices, typed: &[] };
                let _ = editor.process_byte::<Commands, _>(byte, &mut dispatch);
            }
            if self.line_len < self.line.len() {
                self.line[self.line_len] = byte;
                self.line_len += 1;
            }
        }

        if count > 1 {
            // Still a choice to make. The crate has merged everything the
            // candidates agree on, so this says WHY nothing more appeared --
            // otherwise `po` completing to `po` looks like a dead key.
            let _ = editor.write(|writer| {
                for name in &names[..shown] {
                    writer.write_str(name)?;
                    writer.write_str("  ")?;
                }
                Ok(())
            });
            return;
        }

        // One candidate, now fully typed: show what can follow it, with argument
        // syntax. `i2c` + TAB is not ambiguous -- it is a complete word AND a
        // family, and the question at that point is what comes next.
        let line = core::str::from_utf8(&self.line[..self.line_len]).unwrap_or("");
        let mut rows = [("", ""); CANDIDATES];
        let under = crate::shell::rows_under(line, &mut rows);
        if under == 0 {
            return;
        }
        let _ = editor.write(|writer| {
            for (entry, summary) in &rows[..under.min(rows.len())] {
                writer.write_str("\r\n  ")?;
                writer.write_str(entry)?;
                for _ in entry.len()..crate::shell::HELP_WIDTH {
                    writer.write_str(" ")?;
                }
                writer.write_str(summary)?;
            }
            Ok(())
        });
    }
}

/// The shadowed line's capacity, matching the editor's own command buffer.
const LINE: usize = 64;

/// What one byte turned out to mean.
///
/// `embedded-cli` has its own `ControlInput`, and it covers backspace, enter,
/// tab and the four arrows. This covers those PLUS the ones it does not decode,
/// so there is one place that knows what a keystroke was.
#[derive(Clone, Copy, PartialEq, Eq)]
enum Key {
    Text(u8),
    Backspace,
    Delete,
    Left,
    Right,
    Home,
    End,
    KillToEnd,
    KillToStart,
    KillWord,
    History,
    Enter,
    Abort,
    Tab,
    None,
}

/// The two sequences this module sends back into the crate.
const LEFT: &[u8] = b"\x1b[D";
const RIGHT: &[u8] = b"\x1b[C";

/// What each console calls itself in its prompt.
///
/// The index is the index into `target::UART_BASES`, so `aux` is the USB CDC-ACM
/// node and `apollo` is the port on the shared JTAG pins. Naming the port in the
/// prompt is the point: two terminals open on one board otherwise look
/// identical, and only one of them may be spoken to first.
const PORT_NAMES: [&str; MAX_CONSOLES] = ["aux", "apollo", "?", "?"];

/// Longest prompt: `HH:MM:SS.mmm apollo> ` is 21.
const PROMPT_LEN: usize = 24;

/// Per-console prompt text, rebuilt before each new prompt.
///
/// **`Cli::set_prompt` takes `&'static str`**, so the buffer has to outlive
/// every borrow the crate keeps of it -- which a `static` does and a field of
/// `Shell` does not. One per console, because the two carry different names and
/// are edited independently.
///
/// `UnsafeCell` rather than `static mut`: the 2024 edition rejects references to
/// `static mut`, and this needs a `&'static str` into the buffer. Sound here for
/// a reason worth stating -- `Shell::poll` is the only writer, it runs in normal
/// context on one core, and index `i` is only ever touched by console `i`.
struct Prompts(core::cell::UnsafeCell<[[u8; PROMPT_LEN]; MAX_CONSOLES]>);

// SAFETY: see the comment above -- single core, single writer per slot.
unsafe impl Sync for Prompts {}

static PROMPTS: Prompts = Prompts(core::cell::UnsafeCell::new(
    [[0u8; PROMPT_LEN]; MAX_CONSOLES],
));

/// Rebuild this console's prompt around the time now.
///
/// `HH:MM:SS.mmm aux> ` once `time set` has been used, and the same uptime
/// stamp the log column carries until then -- so a prompt and a log line can be
/// read against each other without converting anything.
///
/// Called when Enter arrives, which is the moment before the crate prints the
/// next prompt. The line being submitted is redrawn once with the fresh stamp,
/// so what is on screen is the time the command was entered.
fn refresh_prompt(index: usize, editor: &mut Editor) {
    use crate::shell::parse::FixedWriter;

    // SAFETY: single core, normal context, and slot `index` belongs to this
    // console alone.
    let slot = unsafe { &mut (*PROMPTS.0.get())[index] };
    let mut out = FixedWriter::new(slot);
    let millis = crate::timer::millis();
    match crate::log::wall() {
        Some(seconds) => {
            let _ = write!(out, "{}.{:03} ", crate::log::Clock(seconds), millis % 1000);
        }
        None => {
            let _ = write!(out, "{} ", crate::log::now());
        }
    }
    let _ = write!(out, "{}> ", PORT_NAMES[index]);

    // SAFETY: the buffer is `static`, so this borrow is genuinely `'static`;
    // `FixedWriter` zeroes first and only ever writes ASCII, so the prefix up to
    // the first NUL is valid UTF-8.
    let text: &'static str = unsafe {
        let slot = &(*PROMPTS.0.get())[index];
        let end = slot.iter().position(|&b| b == 0).unwrap_or(slot.len());
        core::str::from_utf8_unchecked(&slot[..end])
    };
    let _ = editor.set_prompt(text);
}

/// Which line editor this build carries, for `info`.
///
/// Swapping the editor must move this string with it. A board that reports the
/// editor it does not have is worse than one that reports nothing.
pub(crate) const EDITOR: &str = "embedded-cli 0.2.1";

/// What it responds to, so "TAB does nothing" can be told apart from "TAB never
/// arrived". Every one of these needs a RAW terminal: in canonical mode the tty
/// handles TAB itself and buffers the line until Enter, so none of them reach
/// the board.
pub(crate) const EDITOR_KEYS: &str = "TAB completes, up/down history, raw tty needed";

