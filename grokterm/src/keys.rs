//! Host key bindings matching public GrokTerm roles.

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};

/// Host-level actions bound to chords.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum KeyAction {
    NewShell,
    NewGrok,
    Manager,
    Voice,
    Quit,
    NextTab,
    PrevTab,
    CloseTab,
}

/// Public binding table (product keys from grokterm.com).
///
/// - Ctrl+T — new shell
/// - Ctrl+B — new Grok tab
/// - Ctrl+G — manager
/// - Ctrl+V — voice
/// - Ctrl+Q — quit
pub const KEY_BINDINGS: &[(KeyAction, &str)] = &[
    (KeyAction::NewShell, "Ctrl+T"),
    (KeyAction::NewGrok, "Ctrl+B"),
    (KeyAction::Manager, "Ctrl+G"),
    (KeyAction::Voice, "Ctrl+V"),
    (KeyAction::Quit, "Ctrl+Q"),
    (KeyAction::NextTab, "Ctrl+Tab / Ctrl+PageDown"),
    (KeyAction::PrevTab, "Ctrl+Shift+Tab / Ctrl+PageUp"),
    (KeyAction::CloseTab, "Ctrl+W"),
];

/// Map a key event to a host action, if any.
pub fn map_key(ev: KeyEvent) -> Option<KeyAction> {
    let ctrl = ev.modifiers.contains(KeyModifiers::CONTROL);
    match (ctrl, ev.code) {
        (true, KeyCode::Char('t')) => Some(KeyAction::NewShell),
        (true, KeyCode::Char('b')) => Some(KeyAction::NewGrok),
        (true, KeyCode::Char('g')) => Some(KeyAction::Manager),
        (true, KeyCode::Char('v')) => Some(KeyAction::Voice),
        (true, KeyCode::Char('q')) => Some(KeyAction::Quit),
        (true, KeyCode::Char('w')) => Some(KeyAction::CloseTab),
        (true, KeyCode::Tab) => {
            if ev.modifiers.contains(KeyModifiers::SHIFT) {
                Some(KeyAction::PrevTab)
            } else {
                Some(KeyAction::NextTab)
            }
        }
        (true, KeyCode::PageDown) => Some(KeyAction::NextTab),
        (true, KeyCode::PageUp) => Some(KeyAction::PrevTab),
        _ => None,
    }
}

/// Human-readable binding help for CLI / manager.
pub fn bindings_help() -> String {
    let mut lines = vec!["Host key bindings:".to_string()];
    for (action, chord) in KEY_BINDINGS {
        lines.push(format!("  {chord:28} {action:?}"));
    }
    lines.join("\n")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn product_keys_map_correctly() {
        let t = KeyEvent::new(KeyCode::Char('t'), KeyModifiers::CONTROL);
        assert_eq!(map_key(t), Some(KeyAction::NewShell));
        let b = KeyEvent::new(KeyCode::Char('b'), KeyModifiers::CONTROL);
        assert_eq!(map_key(b), Some(KeyAction::NewGrok));
        let g = KeyEvent::new(KeyCode::Char('g'), KeyModifiers::CONTROL);
        assert_eq!(map_key(g), Some(KeyAction::Manager));
        let v = KeyEvent::new(KeyCode::Char('v'), KeyModifiers::CONTROL);
        assert_eq!(map_key(v), Some(KeyAction::Voice));
        let q = KeyEvent::new(KeyCode::Char('q'), KeyModifiers::CONTROL);
        assert_eq!(map_key(q), Some(KeyAction::Quit));
    }

    #[test]
    fn bindings_table_documents_roles() {
        let help = bindings_help();
        assert!(help.contains("Ctrl+T"));
        assert!(help.contains("Ctrl+B"));
        assert!(help.contains("Ctrl+G"));
        assert!(help.contains("Ctrl+V"));
        assert!(KEY_BINDINGS.iter().any(|(a, _)| *a == KeyAction::NewShell));
        assert!(KEY_BINDINGS.iter().any(|(a, _)| *a == KeyAction::NewGrok));
        assert!(KEY_BINDINGS.iter().any(|(a, _)| *a == KeyAction::Manager));
        assert!(KEY_BINDINGS.iter().any(|(a, _)| *a == KeyAction::Voice));
    }
}
