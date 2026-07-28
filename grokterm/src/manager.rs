//! Shared manager control plane — same actions for shell, Grok, and voice.

use crate::grok_path::{missing_grok_message, resolve_grok_binary, GrokBinary};
use crate::tab::{TabId, TabInfo, TabKind, TabModel};
use serde::{Deserialize, Serialize};
use std::path::PathBuf;

/// Commands the manager (and voice) can issue.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum ManagerCommand {
    Help,
    OpenShell { title: Option<String> },
    OpenGrok { title: Option<String>, args: Vec<String> },
    Close { id: Option<TabId> },
    List,
    Switch { id: TabId },
    Cycle { forward: bool },
    Rename { id: Option<TabId>, title: String },
    Quit,
}

/// Outcome of applying a command (pure model side; spawn is separate).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum ManagerResponse {
    HelpText(String),
    Opened {
        id: TabId,
        kind: TabKind,
        title: String,
        /// When kind is Grok, the resolved binary (or error text if missing).
        grok: Option<GrokOpenResult>,
    },
    Closed {
        id: TabId,
        remaining: usize,
    },
    Listed {
        tabs: Vec<TabInfo>,
        active: Option<TabId>,
    },
    Switched {
        id: TabId,
    },
    Renamed {
        id: TabId,
        title: String,
    },
    Error(String),
    Quit,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum GrokOpenResult {
    Ready {
        path: PathBuf,
        args: Vec<String>,
    },
    Missing {
        message: String,
    },
}

/// Manager holds the tab model and applies control-plane commands.
#[derive(Debug, Default)]
pub struct Manager {
    pub tabs: TabModel,
    /// Optional override for Grok binary (tests / config).
    pub grok_override: Option<PathBuf>,
    /// When true, OpenGrok still records a tab even if binary is missing
    /// (host may show error; tests assert Missing). Default true for UX.
    pub open_grok_tab_when_missing: bool,
}

impl Manager {
    pub fn new() -> Self {
        Self {
            open_grok_tab_when_missing: true,
            ..Default::default()
        }
    }

    /// Parse a manager line (e.g. from the manager panel input).
    pub fn parse(line: &str) -> Result<ManagerCommand, String> {
        let line = line.trim();
        if line.is_empty() {
            return Err("empty command".into());
        }
        let mut parts = line.split_whitespace();
        let head = parts.next().unwrap().to_ascii_lowercase();
        match head.as_str() {
            "help" | "?" | "h" => Ok(ManagerCommand::Help),
            "shell" | "new" | "sh" => {
                let title = parts.next().map(|s| s.to_string());
                Ok(ManagerCommand::OpenShell { title })
            }
            "grok" | "g" => {
                let rest: Vec<String> = parts.map(|s| s.to_string()).collect();
                let (title, args) = if rest.first().map(|s| s.starts_with('-')).unwrap_or(false) {
                    (None, rest)
                } else if rest.is_empty() {
                    (None, vec![])
                } else {
                    // first token as optional title if not a flag
                    let mut r = rest;
                    let title = Some(r.remove(0));
                    (title, r)
                };
                Ok(ManagerCommand::OpenGrok { title, args })
            }
            "close" | "x" => {
                let id = match parts.next() {
                    Some(s) => Some(TabId(
                        s.parse()
                            .map_err(|_| format!("invalid tab id: {s}"))?,
                    )),
                    None => None,
                };
                Ok(ManagerCommand::Close { id })
            }
            "list" | "ls" | "tabs" => Ok(ManagerCommand::List),
            "switch" | "sel" | "select" => {
                let s = parts.next().ok_or("switch requires tab id")?;
                let id = TabId(s.parse().map_err(|_| format!("invalid tab id: {s}"))?);
                Ok(ManagerCommand::Switch { id })
            }
            "next" => Ok(ManagerCommand::Cycle { forward: true }),
            "prev" | "previous" => Ok(ManagerCommand::Cycle { forward: false }),
            "rename" => {
                let a = parts.next().ok_or("rename requires id and/or title")?;
                if let Ok(n) = a.parse::<u64>() {
                    let title = parts.collect::<Vec<_>>().join(" ");
                    if title.is_empty() {
                        return Err("rename requires a title".into());
                    }
                    Ok(ManagerCommand::Rename {
                        id: Some(TabId(n)),
                        title,
                    })
                } else {
                    let mut title = a.to_string();
                    let rest = parts.collect::<Vec<_>>().join(" ");
                    if !rest.is_empty() {
                        title.push(' ');
                        title.push_str(&rest);
                    }
                    Ok(ManagerCommand::Rename { id: None, title })
                }
            }
            "quit" | "exit" | "q" => Ok(ManagerCommand::Quit),
            other => Err(format!(
                "unknown command: {other}. Type `help` for available commands."
            )),
        }
    }

    pub fn help_text() -> String {
        [
            "GrokTerm manager — shared control plane",
            "",
            "  help                 Show this help",
            "  shell [title]        Open a new shell tab (PTY)",
            "  grok [title] [args]  Open a Grok Build tab (real CLI)",
            "  list                 List tabs",
            "  switch <id>          Switch to tab id",
            "  next / prev          Cycle tabs",
            "  close [id]           Close active or given tab",
            "  rename [id] <title>  Rename active or given tab",
            "  quit                 Quit host",
            "",
            "Keys: Ctrl+T shell · Ctrl+B grok · Ctrl+G manager · Ctrl+V voice · Ctrl+Q quit",
        ]
        .join("\n")
    }

    /// Apply a command to the tab model. Does not spawn processes —
    /// returns enough info for the host to spawn (Grok path/argv).
    pub fn apply(&mut self, cmd: ManagerCommand) -> ManagerResponse {
        match cmd {
            ManagerCommand::Help => ManagerResponse::HelpText(Self::help_text()),
            ManagerCommand::OpenShell { title } => {
                let n = self.tabs.len() + 1;
                let title = title.unwrap_or_else(|| format!("shell-{n}"));
                let id = self.tabs.open(TabKind::Shell, title.clone());
                ManagerResponse::Opened {
                    id,
                    kind: TabKind::Shell,
                    title,
                    grok: None,
                }
            }
            ManagerCommand::OpenGrok { title, args } => {
                let resolved = resolve_grok_binary(self.grok_override.as_deref());
                let grok = match resolved {
                    Ok(bin) => GrokOpenResult::Ready {
                        path: bin.path,
                        args: args.clone(),
                    },
                    Err(_) => GrokOpenResult::Missing {
                        message: missing_grok_message(),
                    },
                };

                let should_open = match &grok {
                    GrokOpenResult::Ready { .. } => true,
                    GrokOpenResult::Missing { .. } => self.open_grok_tab_when_missing,
                };

                if !should_open {
                    return ManagerResponse::Error(missing_grok_message());
                }

                let n = self.tabs.len() + 1;
                let title = title.unwrap_or_else(|| format!("grok-{n}"));
                let id = self.tabs.open(TabKind::Grok, title.clone());
                ManagerResponse::Opened {
                    id,
                    kind: TabKind::Grok,
                    title,
                    grok: Some(grok),
                }
            }
            ManagerCommand::Close { id } => {
                let id = match id.or_else(|| self.tabs.active_id()) {
                    Some(id) => id,
                    None => return ManagerResponse::Error("no tab to close".into()),
                };
                if !self.tabs.close(id) {
                    return ManagerResponse::Error(format!("unknown tab {id}"));
                }
                ManagerResponse::Closed {
                    id,
                    remaining: self.tabs.len(),
                }
            }
            ManagerCommand::List => ManagerResponse::Listed {
                tabs: self.tabs.list().to_vec(),
                active: self.tabs.active_id(),
            },
            ManagerCommand::Switch { id } => {
                if self.tabs.switch(id) {
                    ManagerResponse::Switched { id }
                } else {
                    ManagerResponse::Error(format!("unknown tab {id}"))
                }
            }
            ManagerCommand::Cycle { forward } => match self.tabs.cycle(forward) {
                Some(id) => ManagerResponse::Switched { id },
                None => ManagerResponse::Error("no tabs".into()),
            },
            ManagerCommand::Rename { id, title } => {
                let id = match id.or_else(|| self.tabs.active_id()) {
                    Some(id) => id,
                    None => return ManagerResponse::Error("no tab to rename".into()),
                };
                if self.tabs.rename(id, title.clone()) {
                    ManagerResponse::Renamed { id, title }
                } else {
                    ManagerResponse::Error(format!("unknown tab {id}"))
                }
            }
            ManagerCommand::Quit => ManagerResponse::Quit,
        }
    }

    /// Resolve Grok for spawn without opening a tab.
    pub fn resolve_grok(&self) -> Result<GrokBinary, crate::grok_path::GrokPathError> {
        resolve_grok_binary(self.grok_override.as_deref())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::tempdir;

    #[test]
    fn parse_and_apply_shell_list_switch_close() {
        let mut m = Manager::new();
        assert_eq!(
            Manager::parse("help").unwrap(),
            ManagerCommand::Help
        );
        let r = m.apply(ManagerCommand::Help);
        assert!(matches!(r, ManagerResponse::HelpText(t) if t.contains("shell")));

        let r = m.apply(ManagerCommand::OpenShell {
            title: Some("alpha".into()),
        });
        let id_a = match r {
            ManagerResponse::Opened { id, kind, title, .. } => {
                assert_eq!(kind, TabKind::Shell);
                assert_eq!(title, "alpha");
                id
            }
            other => panic!("unexpected {other:?}"),
        };

        let r = m.apply(ManagerCommand::OpenShell { title: None });
        let id_b = match r {
            ManagerResponse::Opened { id, .. } => id,
            other => panic!("unexpected {other:?}"),
        };
        assert_eq!(m.tabs.len(), 2);
        assert_eq!(m.tabs.active_id(), Some(id_b));

        let r = m.apply(ManagerCommand::List);
        match r {
            ManagerResponse::Listed { tabs, active } => {
                assert_eq!(tabs.len(), 2);
                assert_eq!(active, Some(id_b));
            }
            other => panic!("unexpected {other:?}"),
        }

        let r = m.apply(ManagerCommand::Switch { id: id_a });
        assert_eq!(r, ManagerResponse::Switched { id: id_a });
        assert_eq!(m.tabs.active_id(), Some(id_a));

        let r = m.apply(ManagerCommand::Close { id: None });
        assert_eq!(
            r,
            ManagerResponse::Closed {
                id: id_a,
                remaining: 1
            }
        );
        assert_eq!(m.tabs.active_id(), Some(id_b));
    }

    #[test]
    fn open_grok_records_spawn_argv_when_binary_present() {
        let dir = tempdir().unwrap();
        let fake = dir.path().join("grok");
        fs::write(&fake, b"#!/bin/sh\nexit 0\n").unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mut perms = fs::metadata(&fake).unwrap().permissions();
            perms.set_mode(0o755);
            fs::set_permissions(&fake, perms).unwrap();
        }

        let mut m = Manager::new();
        m.grok_override = Some(fake.clone());
        let r = m.apply(ManagerCommand::OpenGrok {
            title: Some("agent".into()),
            args: vec!["--version".into()],
        });
        match r {
            ManagerResponse::Opened {
                kind: TabKind::Grok,
                title,
                grok: Some(GrokOpenResult::Ready { path, args }),
                ..
            } => {
                assert_eq!(title, "agent");
                assert_eq!(path, fake);
                assert_eq!(args, vec!["--version".to_string()]);
            }
            other => panic!("unexpected {other:?}"),
        }
    }

    #[test]
    fn open_grok_missing_returns_clear_message() {
        let dir = tempdir().unwrap();
        let missing = dir.path().join("definitely-missing-grok-binary");
        let mut m = Manager::new();
        m.grok_override = Some(missing);
        let r = m.apply(ManagerCommand::OpenGrok {
            title: None,
            args: vec![],
        });
        match r {
            ManagerResponse::Opened {
                kind: TabKind::Grok,
                grok: Some(GrokOpenResult::Missing { message }),
                ..
            } => {
                assert!(
                    message.contains("Grok Build CLI not found"),
                    "{message}"
                );
            }
            other => panic!("expected Missing GrokOpenResult, got {other:?}"),
        }

        // When open_grok_tab_when_missing is false, surface Error instead.
        let mut m = Manager::new();
        m.grok_override = Some(dir.path().join("still-missing"));
        m.open_grok_tab_when_missing = false;
        let r = m.apply(ManagerCommand::OpenGrok {
            title: None,
            args: vec![],
        });
        match r {
            ManagerResponse::Error(message) => {
                assert!(message.contains("Grok Build CLI not found"), "{message}");
            }
            other => panic!("expected Error, got {other:?}"),
        }
    }

    #[test]
    fn parse_unknown_command() {
        assert!(Manager::parse("frobnicate").is_err());
    }
}
