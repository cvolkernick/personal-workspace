//! GrokTerm library — multi-tab PTY host, manager control plane, voice dispatch.
//!
//! Clean-room reimplementation of the publicly described GrokTerm product
//! (https://grokterm.com / @Daniel_Farinax announcement).

pub mod grok_path;
pub mod host;
pub mod keys;
pub mod manager;
pub mod pty_session;
pub mod tab;
pub mod voice;

pub use grok_path::{resolve_grok_binary, GrokBinary, GrokPathError};
pub use host::Host;
pub use keys::{KeyAction, KEY_BINDINGS};
pub use manager::{Manager, ManagerCommand, ManagerResponse};
pub use pty_session::{PtySession, SessionKind, SpawnSpec};
pub use tab::{TabId, TabInfo, TabKind, TabModel};
pub use voice::{dispatch_voice_intent, VoiceIntent};

/// Product identity shown by `--version` / `--help`.
pub const PRODUCT_NAME: &str = "GrokTerm";
pub const PRODUCT_VERSION: &str = env!("CARGO_PKG_VERSION");
pub const PRODUCT_DESCRIPTION: &str =
    "Multi-tab terminal host for Grok Build — PTY sessions, manager control plane, two-way voice";
