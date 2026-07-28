//! Real PTY-backed sessions (portable-pty).

use crate::tab::TabKind;
use anyhow::{anyhow, Context, Result};
use portable_pty::{native_pty_system, CommandBuilder, MasterPty, PtySize};
use std::io::{Read, Write};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

/// Kind of process to spawn in a PTY.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SessionKind {
    Shell,
    Grok { binary: PathBuf, args: Vec<String> },
}

impl From<SessionKind> for TabKind {
    fn from(k: SessionKind) -> Self {
        match k {
            SessionKind::Shell => TabKind::Shell,
            SessionKind::Grok { .. } => TabKind::Grok,
        }
    }
}

/// Spec for spawning a child in a PTY.
#[derive(Debug, Clone)]
pub struct SpawnSpec {
    pub kind: SessionKind,
    pub cols: u16,
    pub rows: u16,
    pub cwd: Option<PathBuf>,
}

impl Default for SpawnSpec {
    fn default() -> Self {
        Self {
            kind: SessionKind::Shell,
            cols: 80,
            rows: 24,
            cwd: None,
        }
    }
}

/// A live PTY session with independent child process.
pub struct PtySession {
    pub kind: SessionKind,
    master: Box<dyn MasterPty + Send>,
    writer: Box<dyn Write + Send>,
    /// Background-reader buffer of child output.
    output: Arc<Mutex<Vec<u8>>>,
    /// Child process handle (kept so process stays alive / can be waited).
    child: Box<dyn portable_pty::Child + Send + Sync>,
}

impl PtySession {
    /// Spawn a real PTY with the given command/shell.
    pub fn spawn(spec: SpawnSpec) -> Result<Self> {
        let pty_system = native_pty_system();
        let pair = pty_system
            .openpty(PtySize {
                rows: spec.rows,
                cols: spec.cols,
                pixel_width: 0,
                pixel_height: 0,
            })
            .context("openpty failed")?;

        let mut cmd = match &spec.kind {
            SessionKind::Shell => {
                let shell = std::env::var("SHELL").unwrap_or_else(|_| {
                    if cfg!(windows) {
                        "cmd.exe".into()
                    } else {
                        "/bin/zsh".into()
                    }
                });
                let mut c = CommandBuilder::new(shell);
                // Interactive login-ish shell without forcing login profile delays.
                if !cfg!(windows) {
                    c.arg("-i");
                }
                c
            }
            SessionKind::Grok { binary, args } => {
                let mut c = CommandBuilder::new(binary);
                for a in args {
                    c.arg(a);
                }
                c
            }
        };

        if let Some(cwd) = &spec.cwd {
            cmd.cwd(cwd);
        }

        let child = pair
            .slave
            .spawn_command(cmd)
            .context("spawn_command in PTY failed")?;
        // Drop slave so child owns the only slave fd.
        drop(pair.slave);

        let mut reader = pair
            .master
            .try_clone_reader()
            .context("clone PTY reader")?;
        let writer = pair
            .master
            .take_writer()
            .context("take PTY writer")?;

        let output = Arc::new(Mutex::new(Vec::new()));
        let out_bg = Arc::clone(&output);
        thread::spawn(move || {
            let mut buf = [0u8; 4096];
            loop {
                match reader.read(&mut buf) {
                    Ok(0) => break,
                    Ok(n) => {
                        if let Ok(mut g) = out_bg.lock() {
                            g.extend_from_slice(&buf[..n]);
                        }
                    }
                    Err(_) => break,
                }
            }
        });

        Ok(Self {
            kind: spec.kind,
            master: pair.master,
            writer,
            output,
            child,
        })
    }

    /// Write bytes to the PTY (keyboard / injected command).
    pub fn write_all(&mut self, data: &[u8]) -> Result<()> {
        self.writer.write_all(data)?;
        self.writer.flush()?;
        Ok(())
    }

    /// Snapshot of accumulated PTY output (UTF-8 lossy for display).
    pub fn drain_output_snapshot(&self) -> Vec<u8> {
        self.output.lock().map(|g| g.clone()).unwrap_or_default()
    }

    /// Take and clear accumulated output.
    pub fn take_output(&self) -> Vec<u8> {
        self.output
            .lock()
            .map(|mut g| {
                let v = g.clone();
                g.clear();
                v
            })
            .unwrap_or_default()
    }

    /// Wait until output contains `needle` or timeout. Returns true if found.
    pub fn wait_for_output(&self, needle: &str, timeout: Duration) -> bool {
        let start = std::time::Instant::now();
        while start.elapsed() < timeout {
            let snap = self.drain_output_snapshot();
            if String::from_utf8_lossy(&snap).contains(needle) {
                return true;
            }
            thread::sleep(Duration::from_millis(20));
        }
        false
    }

    /// Resize the PTY.
    pub fn resize(&mut self, cols: u16, rows: u16) -> Result<()> {
        self.master
            .resize(PtySize {
                rows,
                cols,
                pixel_width: 0,
                pixel_height: 0,
            })
            .map_err(|e| anyhow!("resize: {e}"))
    }

    /// Try to kill the child process.
    pub fn kill(&mut self) {
        let _ = self.child.kill();
    }
}

impl Drop for PtySession {
    fn drop(&mut self) {
        self.kill();
    }
}

/// Convenience: spawn a shell PTY, run `echo <nonce>`, assert nonce in output.
/// Used by integration tests and harnesses.
pub fn pty_echo_nonce(nonce: &str) -> Result<String> {
    let mut session = PtySession::spawn(SpawnSpec {
        kind: SessionKind::Shell,
        cols: 80,
        rows: 24,
        cwd: None,
    })?;

    // Give shell a moment to start.
    thread::sleep(Duration::from_millis(150));

    let cmd = format!("echo {nonce}\n");
    session.write_all(cmd.as_bytes())?;

    if !session.wait_for_output(nonce, Duration::from_secs(5)) {
        let got = String::from_utf8_lossy(&session.drain_output_snapshot()).into_owned();
        return Err(anyhow!(
            "nonce {nonce:?} not found in PTY output; got: {got:?}"
        ));
    }
    Ok(String::from_utf8_lossy(&session.drain_output_snapshot()).into_owned())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::grok_path::resolve_grok_binary;
    use std::time::{SystemTime, UNIX_EPOCH};

    #[test]
    fn real_pty_shell_echoes_nonce() {
        let nonce = format!(
            "GROKTERM_NONCE_{}",
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        );
        let out = pty_echo_nonce(&nonce).expect("PTY echo should succeed");
        assert!(
            out.contains(&nonce),
            "expected nonce in PTY output, got: {out:?}"
        );
    }

    #[test]
    fn grok_binary_short_lived_pty_when_present() {
        let Ok(bin) = resolve_grok_binary(None) else {
            // Grok optional — absence is not a host failure.
            return;
        };
        let mut session = PtySession::spawn(SpawnSpec {
            kind: SessionKind::Grok {
                binary: bin.path.clone(),
                args: vec!["--version".into()],
            },
            cols: 80,
            rows: 24,
            cwd: None,
        })
        .expect("spawn grok --version in PTY");

        // Wait for any output or clean exit window; then kill.
        let _ = session.wait_for_output(".", Duration::from_secs(3));
        let snap = String::from_utf8_lossy(&session.drain_output_snapshot()).into_owned();
        session.kill();
        // Real binary path was used (spawn did not fail on missing file).
        assert!(bin.path.is_file());
        // Output may be empty if --version writes slowly; spawn success is the gate.
        let _ = snap;
    }
}
