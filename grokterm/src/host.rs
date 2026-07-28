//! Host: coordinates manager, PTY sessions, and TUI.

use crate::keys::{map_key, KeyAction};
use crate::manager::{GrokOpenResult, Manager, ManagerCommand, ManagerResponse};
use crate::pty_session::{PtySession, SessionKind, SpawnSpec};
use crate::tab::{TabId, TabKind};
use crate::voice::{dispatch_voice_intent, voice_entry_help, VoiceIntent};
use anyhow::{Context, Result};
use crossterm::event::{self, Event, KeyCode, KeyEvent, KeyModifiers};
use crossterm::execute;
use crossterm::terminal::{
    disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen,
};
use ratatui::backend::CrosstermBackend;
use ratatui::layout::{Constraint, Direction, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, Paragraph, Tabs as TabBar, Wrap};
use ratatui::Terminal;
use std::collections::HashMap;
use std::io::{self, Write};
use std::path::PathBuf;
use std::time::Duration;

/// Overlay panel mode.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Overlay {
    None,
    Manager,
    Voice,
}

/// Runtime host state.
pub struct Host {
    manager: Manager,
    sessions: HashMap<TabId, PtySession>,
    /// Scrollback/display buffer per tab (accumulated PTY output as text).
    buffers: HashMap<TabId, String>,
    overlay: Overlay,
    manager_input: String,
    manager_log: Vec<String>,
    status: String,
    voice_mode: bool,
    should_quit: bool,
}

impl Host {
    pub fn new() -> Self {
        Self {
            manager: Manager::new(),
            sessions: HashMap::new(),
            buffers: HashMap::new(),
            overlay: Overlay::None,
            manager_input: String::new(),
            manager_log: vec![
                "GrokTerm host ready. Ctrl+T shell · Ctrl+B grok · Ctrl+G manager · Ctrl+V voice"
                    .into(),
            ],
            status: "ready".into(),
            voice_mode: false,
            should_quit: false,
        }
    }

    pub fn with_grok_override(mut self, path: Option<PathBuf>) -> Self {
        self.manager.grok_override = path;
        self
    }

    /// Open initial shell tab (and optionally a Grok tab).
    pub fn bootstrap(&mut self, open_grok: bool) -> Result<()> {
        self.apply_and_spawn(ManagerCommand::OpenShell {
            title: Some("shell".into()),
        })?;
        if open_grok {
            self.apply_and_spawn(ManagerCommand::OpenGrok {
                title: Some("grok".into()),
                args: vec![],
            })?;
        }
        Ok(())
    }

    fn apply_and_spawn(&mut self, cmd: ManagerCommand) -> Result<ManagerResponse> {
        let resp = self.manager.apply(cmd);
        self.handle_response(&resp)?;
        Ok(resp)
    }

    fn handle_response(&mut self, resp: &ManagerResponse) -> Result<()> {
        match resp {
            ManagerResponse::Opened {
                id,
                kind,
                title,
                grok,
            } => {
                let spec = match kind {
                    TabKind::Shell => SpawnSpec {
                        kind: SessionKind::Shell,
                        ..Default::default()
                    },
                    TabKind::Grok => match grok {
                        Some(GrokOpenResult::Ready { path, args }) => SpawnSpec {
                            kind: SessionKind::Grok {
                                binary: path.clone(),
                                args: args.clone(),
                            },
                            ..Default::default()
                        },
                        Some(GrokOpenResult::Missing { message }) => {
                            self.status = message.clone();
                            self.manager_log.push(format!("error: {message}"));
                            // No PTY when missing; keep tab as placeholder buffer.
                            self.buffers
                                .insert(*id, format!("[Grok unavailable]\n{message}\n"));
                            self.status = format!("opened {kind} tab {id} ({title}) — no binary");
                            return Ok(());
                        }
                        None => SpawnSpec {
                            kind: SessionKind::Shell,
                            ..Default::default()
                        },
                    },
                };

                match PtySession::spawn(spec) {
                    Ok(session) => {
                        self.sessions.insert(*id, session);
                        self.buffers.entry(*id).or_default();
                        self.status = format!("opened {kind} tab {id} ({title})");
                        self.manager_log
                            .push(format!("opened {kind} tab {id}: {title}"));
                    }
                    Err(e) => {
                        let msg = format!("failed to spawn {kind} tab: {e}");
                        self.status = msg.clone();
                        self.manager_log.push(msg);
                        self.buffers
                            .insert(*id, format!("[spawn failed]\n{e}\n"));
                    }
                }
            }
            ManagerResponse::Closed { id, remaining } => {
                if let Some(mut s) = self.sessions.remove(id) {
                    s.kill();
                }
                self.buffers.remove(id);
                self.status = format!("closed tab {id}; {remaining} remaining");
                self.manager_log.push(format!("closed tab {id}"));
            }
            ManagerResponse::Listed { tabs, active } => {
                let mut lines = vec![format!("tabs ({}):", tabs.len())];
                for t in tabs {
                    let mark = if Some(t.id) == *active { "*" } else { " " };
                    lines.push(format!("  {mark} [{}] {} ({})", t.id, t.title, t.kind));
                }
                self.manager_log.extend(lines);
            }
            ManagerResponse::Switched { id } => {
                self.status = format!("switched to tab {id}");
                self.manager_log.push(format!("switch {id}"));
            }
            ManagerResponse::HelpText(t) => {
                for line in t.lines() {
                    self.manager_log.push(line.to_string());
                }
            }
            ManagerResponse::Renamed { id, title } => {
                self.manager_log
                    .push(format!("renamed tab {id} -> {title}"));
            }
            ManagerResponse::Error(e) => {
                self.status = e.clone();
                self.manager_log.push(format!("error: {e}"));
            }
            ManagerResponse::Quit => {
                self.should_quit = true;
            }
        }
        // Cap log size
        if self.manager_log.len() > 200 {
            let drain = self.manager_log.len() - 200;
            self.manager_log.drain(0..drain);
        }
        Ok(())
    }

    fn pump_pty_output(&mut self) {
        let ids: Vec<TabId> = self.sessions.keys().copied().collect();
        for id in ids {
            if let Some(session) = self.sessions.get(&id) {
                let chunk = session.take_output();
                if !chunk.is_empty() {
                    let text = String::from_utf8_lossy(&chunk);
                    self.buffers.entry(id).or_default().push_str(&text);
                    // Cap buffer per tab
                    if let Some(buf) = self.buffers.get_mut(&id) {
                        if buf.len() > 200_000 {
                            let keep = buf.len() - 150_000;
                            buf.drain(0..keep);
                        }
                    }
                }
            }
        }
    }

    fn active_session_mut(&mut self) -> Option<&mut PtySession> {
        let id = self.manager.tabs.active_id()?;
        self.sessions.get_mut(&id)
    }

    /// Run the interactive TUI host loop.
    pub fn run_tui(&mut self) -> Result<()> {
        enable_raw_mode().context("enable_raw_mode")?;
        let mut stdout = io::stdout();
        execute!(stdout, EnterAlternateScreen).context("EnterAlternateScreen")?;
        let backend = CrosstermBackend::new(stdout);
        let mut terminal = Terminal::new(backend).context("Terminal::new")?;

        let result = self.event_loop(&mut terminal);

        disable_raw_mode().ok();
        execute!(terminal.backend_mut(), LeaveAlternateScreen).ok();
        terminal.show_cursor().ok();
        result
    }

    fn event_loop(&mut self, terminal: &mut Terminal<CrosstermBackend<io::Stdout>>) -> Result<()> {
        while !self.should_quit {
            self.pump_pty_output();
            terminal.draw(|f| self.draw(f))?;

            if event::poll(Duration::from_millis(50))? {
                match event::read()? {
                    Event::Key(key) => self.on_key(key)?,
                    Event::Resize(c, r) => {
                        for session in self.sessions.values_mut() {
                            let _ = session.resize(c, r.saturating_sub(2).max(1));
                        }
                    }
                    _ => {}
                }
            }
        }
        // Cleanup sessions
        for (_, mut s) in self.sessions.drain() {
            s.kill();
        }
        Ok(())
    }

    fn on_key(&mut self, key: KeyEvent) -> Result<()> {
        // Overlay-specific input first
        match self.overlay {
            Overlay::Manager => {
                if key.modifiers.contains(KeyModifiers::CONTROL)
                    && matches!(key.code, KeyCode::Char('g') | KeyCode::Char('q'))
                {
                    // fall through to host chords
                } else {
                    return self.on_manager_key(key);
                }
            }
            Overlay::Voice => {
                if key.code == KeyCode::Esc
                    || (key.modifiers.contains(KeyModifiers::CONTROL)
                        && key.code == KeyCode::Char('v'))
                {
                    self.overlay = Overlay::None;
                    self.voice_mode = false;
                    self.status = "voice overlay closed".into();
                    return Ok(());
                }
                // Demo dispatch keys while voice overlay open
                return self.on_voice_overlay_key(key);
            }
            Overlay::None => {}
        }

        if let Some(action) = map_key(key) {
            return self.on_action(action);
        }

        // Pass through to active PTY
        if let Some(session) = self.active_session_mut() {
            let bytes = key_to_bytes(key);
            if !bytes.is_empty() {
                let _ = session.write_all(&bytes);
            }
        }
        Ok(())
    }

    fn on_action(&mut self, action: KeyAction) -> Result<()> {
        match action {
            KeyAction::NewShell => {
                self.apply_and_spawn(ManagerCommand::OpenShell { title: None })?;
            }
            KeyAction::NewGrok => {
                self.apply_and_spawn(ManagerCommand::OpenGrok {
                    title: None,
                    args: vec![],
                })?;
            }
            KeyAction::Manager => {
                self.overlay = if self.overlay == Overlay::Manager {
                    Overlay::None
                } else {
                    Overlay::Manager
                };
                self.status = if self.overlay == Overlay::Manager {
                    "manager open — type help"
                } else {
                    "manager closed"
                }
                .into();
            }
            KeyAction::Voice => {
                self.overlay = Overlay::Voice;
                self.voice_mode = true;
                self.status = "voice mode — tools dispatch to manager".into();
                self.manager_log
                    .push(voice_entry_help().to_string());
                self.manager_log.push(
                    "Voice overlay: 1=shell 2=grok 3=list 4=close 5=help Esc=close".into(),
                );
            }
            KeyAction::Quit => {
                self.should_quit = true;
            }
            KeyAction::NextTab => {
                let _ = self.apply_and_spawn(ManagerCommand::Cycle { forward: true })?;
            }
            KeyAction::PrevTab => {
                let _ = self.apply_and_spawn(ManagerCommand::Cycle { forward: false })?;
            }
            KeyAction::CloseTab => {
                let _ = self.apply_and_spawn(ManagerCommand::Close { id: None })?;
            }
        }
        Ok(())
    }

    fn on_manager_key(&mut self, key: KeyEvent) -> Result<()> {
        match key.code {
            KeyCode::Esc => {
                self.overlay = Overlay::None;
                self.status = "manager closed".into();
            }
            KeyCode::Enter => {
                let line = self.manager_input.trim().to_string();
                self.manager_input.clear();
                if line.is_empty() {
                    return Ok(());
                }
                self.manager_log.push(format!("> {line}"));
                match Manager::parse(&line) {
                    Ok(cmd) => {
                        let resp = self.apply_and_spawn(cmd)?;
                        if matches!(resp, ManagerResponse::Quit) {
                            self.should_quit = true;
                        }
                    }
                    Err(e) => self.manager_log.push(format!("error: {e}")),
                }
            }
            KeyCode::Backspace => {
                self.manager_input.pop();
            }
            KeyCode::Char(c) => {
                self.manager_input.push(c);
            }
            _ => {}
        }
        Ok(())
    }

    fn on_voice_overlay_key(&mut self, key: KeyEvent) -> Result<()> {
        let intent = match key.code {
            KeyCode::Char('1') => Some(VoiceIntent::OpenShell {
                title: Some("voice-shell".into()),
            }),
            KeyCode::Char('2') => Some(VoiceIntent::OpenGrok {
                title: Some("voice-grok".into()),
                args: vec![],
            }),
            KeyCode::Char('3') => Some(VoiceIntent::ListTabs),
            KeyCode::Char('4') => Some(VoiceIntent::CloseTab { id: None }),
            KeyCode::Char('5') => Some(VoiceIntent::ManagerHelp),
            _ => None,
        };
        if let Some(intent) = intent {
            // Dispatch through shipped voice path, then honor spawn side-effects
            // by re-applying via handle on a clone of the response.
            // Actually dispatch_voice_intent applies to manager; we need spawn.
            // So: convert intent → command via intent_to_command and apply_and_spawn.
            let cmd = crate::voice::intent_to_command(intent);
            let _ = self.apply_and_spawn(cmd)?;
        }
        Ok(())
    }

    fn draw(&self, f: &mut ratatui::Frame) {
        let area = f.area();
        let chunks = Layout::default()
            .direction(Direction::Vertical)
            .constraints([
                Constraint::Length(1),
                Constraint::Min(3),
                Constraint::Length(1),
            ])
            .split(area);

        self.draw_tab_bar(f, chunks[0]);
        self.draw_body(f, chunks[1]);
        self.draw_status(f, chunks[2]);
    }

    fn draw_tab_bar(&self, f: &mut ratatui::Frame, area: Rect) {
        let tabs = self.manager.tabs.list();
        let titles: Vec<Line> = if tabs.is_empty() {
            vec![Line::from(" (no tabs) ")]
        } else {
            tabs.iter()
                .map(|t| {
                    Line::from(format!(" {} [{}:{}] ", t.title, t.kind, t.id))
                })
                .collect()
        };
        let selected = self
            .manager
            .tabs
            .active_id()
            .and_then(|id| tabs.iter().position(|t| t.id == id))
            .unwrap_or(0);
        let bar = TabBar::new(titles)
            .select(selected)
            .style(Style::default().fg(Color::Gray))
            .highlight_style(
                Style::default()
                    .fg(Color::Black)
                    .bg(Color::Cyan)
                    .add_modifier(Modifier::BOLD),
            )
            .divider(Span::raw("|"));
        f.render_widget(bar, area);
    }

    fn draw_body(&self, f: &mut ratatui::Frame, area: Rect) {
        match self.overlay {
            Overlay::Manager => self.draw_manager(f, area),
            Overlay::Voice => self.draw_voice(f, area),
            Overlay::None => self.draw_session(f, area),
        }
    }

    fn draw_session(&self, f: &mut ratatui::Frame, area: Rect) {
        let text = if let Some(id) = self.manager.tabs.active_id() {
            self.buffers
                .get(&id)
                .cloned()
                .unwrap_or_else(|| "(no output yet)".into())
        } else {
            "No active tab. Press Ctrl+T for a shell.".into()
        };
        // Show last N lines that fit
        let lines: Vec<Line> = text
            .lines()
            .rev()
            .take(area.height as usize)
            .collect::<Vec<_>>()
            .into_iter()
            .rev()
            .map(|l| Line::from(l.to_string()))
            .collect();
        let p = Paragraph::new(lines)
            .block(
                Block::default()
                    .borders(Borders::ALL)
                    .title(" session (PTY) "),
            )
            .wrap(Wrap { trim: false });
        f.render_widget(p, area);
    }

    fn draw_manager(&self, f: &mut ratatui::Frame, area: Rect) {
        let chunks = Layout::default()
            .direction(Direction::Vertical)
            .constraints([Constraint::Min(3), Constraint::Length(3)])
            .split(area);

        let log_lines: Vec<Line> = self
            .manager_log
            .iter()
            .rev()
            .take(chunks[0].height.saturating_sub(2) as usize)
            .collect::<Vec<_>>()
            .into_iter()
            .rev()
            .map(|l| Line::from(l.clone()))
            .collect();
        let log = Paragraph::new(log_lines)
            .block(
                Block::default()
                    .borders(Borders::ALL)
                    .title(" manager control plane "),
            )
            .wrap(Wrap { trim: false });
        f.render_widget(log, chunks[0]);

        let input = Paragraph::new(format!("> {}_", self.manager_input)).block(
            Block::default()
                .borders(Borders::ALL)
                .title(" command (help, shell, grok, list, switch, close, quit) "),
        );
        f.render_widget(input, chunks[1]);
    }

    fn draw_voice(&self, f: &mut ratatui::Frame, area: Rect) {
        let body = format!(
            "{}\n\n\
             Live mic/speaker: best-effort (Grok Voice APIs + audio devices).\n\
             Pure tool dispatch is active — same control plane as manager.\n\n\
             Keys: 1 open shell · 2 open grok · 3 list · 4 close · 5 help · Esc close\n\n\
             Log:\n{}",
            voice_entry_help(),
            self.manager_log
                .iter()
                .rev()
                .take(12)
                .cloned()
                .collect::<Vec<_>>()
                .into_iter()
                .rev()
                .collect::<Vec<_>>()
                .join("\n")
        );
        let p = Paragraph::new(body)
            .block(
                Block::default()
                    .borders(Borders::ALL)
                    .title(" voice (two-way tools → manager) "),
            )
            .wrap(Wrap { trim: false });
        f.render_widget(p, area);
    }

    fn draw_status(&self, f: &mut ratatui::Frame, area: Rect) {
        let mode = match self.overlay {
            Overlay::None => "host",
            Overlay::Manager => "manager",
            Overlay::Voice => "voice",
        };
        let line = format!(
            " GrokTerm v{} │ {} │ {} │ Ctrl+T/B/G/V/Q ",
            crate::PRODUCT_VERSION,
            mode,
            self.status
        );
        let p = Paragraph::new(line).style(Style::default().bg(Color::DarkGray).fg(Color::White));
        f.render_widget(p, area);
    }
}

impl Default for Host {
    fn default() -> Self {
        Self::new()
    }
}

/// Convert a key event into bytes for the PTY.
fn key_to_bytes(key: KeyEvent) -> Vec<u8> {
    match key.code {
        KeyCode::Char(c) => {
            if key.modifiers.contains(KeyModifiers::CONTROL) {
                let cl = c.to_ascii_lowercase();
                if cl.is_ascii_lowercase() {
                    return vec![(cl as u8) - b'a' + 1];
                }
            }
            let mut v = Vec::new();
            write!(v, "{c}").ok();
            v
        }
        KeyCode::Enter => vec![b'\r'],
        KeyCode::Backspace => vec![0x7f],
        KeyCode::Tab => vec![b'\t'],
        KeyCode::Esc => vec![0x1b],
        KeyCode::Up => b"\x1b[A".to_vec(),
        KeyCode::Down => b"\x1b[B".to_vec(),
        KeyCode::Right => b"\x1b[C".to_vec(),
        KeyCode::Left => b"\x1b[D".to_vec(),
        KeyCode::Home => b"\x1b[H".to_vec(),
        KeyCode::End => b"\x1b[F".to_vec(),
        KeyCode::Delete => b"\x1b[3~".to_vec(),
        _ => vec![],
    }
}

/// Non-TUI: run a single manager command line for scripting/tests.
pub fn run_manager_line(manager: &mut Manager, line: &str) -> Result<ManagerResponse, String> {
    let cmd = Manager::parse(line)?;
    Ok(manager.apply(cmd))
}

/// Non-TUI voice dispatch entry used by CLI `--voice` dry path.
pub fn run_voice_demo(manager: &mut Manager) -> Vec<ManagerResponse> {
    let intents = [
        VoiceIntent::ManagerHelp,
        VoiceIntent::OpenShell {
            title: Some("voice-demo-shell".into()),
        },
        VoiceIntent::ListTabs,
    ];
    intents
        .into_iter()
        .map(|i| dispatch_voice_intent(manager, i))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::manager::ManagerResponse;
    use crate::tab::TabKind;

    #[test]
    fn host_bootstrap_opens_shell_session_record() {
        let mut host = Host::new();
        // Only test manager side without requiring interactive TUI —
        // spawn may succeed in this env.
        let r = host
            .apply_and_spawn(ManagerCommand::OpenShell {
                title: Some("t".into()),
            })
            .expect("open shell");
        assert!(matches!(
            r,
            ManagerResponse::Opened {
                kind: TabKind::Shell,
                ..
            }
        ));
        assert_eq!(host.manager.tabs.len(), 1);
        // Session either spawned or recorded error buffer
        let id = host.manager.tabs.active_id().unwrap();
        assert!(
            host.sessions.contains_key(&id) || host.buffers.contains_key(&id),
            "session or error buffer should exist"
        );
        // cleanup
        host.sessions.drain().for_each(|(_, mut s)| s.kill());
    }

    #[test]
    fn manager_line_and_voice_demo_share_control_plane() {
        let mut m = Manager::new();
        let r = run_manager_line(&mut m, "shell alpha").unwrap();
        assert!(matches!(
            r,
            ManagerResponse::Opened {
                kind: TabKind::Shell,
                ..
            }
        ));
        let responses = run_voice_demo(&mut m);
        assert!(responses.iter().any(|r| matches!(r, ManagerResponse::HelpText(_))));
        assert!(responses.iter().any(|r| matches!(
            r,
            ManagerResponse::Opened {
                kind: TabKind::Shell,
                ..
            }
        )));
    }
}
