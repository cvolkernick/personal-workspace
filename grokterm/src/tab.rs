//! Multi-tab state machine (pure logic — no I/O).

use serde::{Deserialize, Serialize};
use std::fmt;

/// Stable identifier for a tab within a host instance.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct TabId(pub u64);

impl fmt::Display for TabId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.0)
    }
}

/// What process a tab hosts.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum TabKind {
    Shell,
    Grok,
}

impl fmt::Display for TabKind {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            TabKind::Shell => write!(f, "shell"),
            TabKind::Grok => write!(f, "grok"),
        }
    }
}

/// Public view of a tab for list/UI.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TabInfo {
    pub id: TabId,
    pub kind: TabKind,
    pub title: String,
}

/// Multi-tab model: open, close, switch, list.
#[derive(Debug, Clone, Default)]
pub struct TabModel {
    next_id: u64,
    tabs: Vec<TabInfo>,
    active: Option<TabId>,
}

impl TabModel {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn len(&self) -> usize {
        self.tabs.len()
    }

    pub fn is_empty(&self) -> bool {
        self.tabs.is_empty()
    }

    pub fn active_id(&self) -> Option<TabId> {
        self.active
    }

    pub fn active_tab(&self) -> Option<&TabInfo> {
        let id = self.active?;
        self.get(id)
    }

    pub fn get(&self, id: TabId) -> Option<&TabInfo> {
        self.tabs.iter().find(|t| t.id == id)
    }

    pub fn list(&self) -> &[TabInfo] {
        &self.tabs
    }

    /// Open a new tab and make it active. Returns the new id.
    pub fn open(&mut self, kind: TabKind, title: impl Into<String>) -> TabId {
        self.next_id += 1;
        let id = TabId(self.next_id);
        let info = TabInfo {
            id,
            kind,
            title: title.into(),
        };
        self.tabs.push(info);
        self.active = Some(id);
        id
    }

    /// Switch active tab. Returns false if id unknown.
    pub fn switch(&mut self, id: TabId) -> bool {
        if self.tabs.iter().any(|t| t.id == id) {
            self.active = Some(id);
            true
        } else {
            false
        }
    }

    /// Switch to next/previous tab (wraps). No-op if empty.
    pub fn cycle(&mut self, forward: bool) -> Option<TabId> {
        if self.tabs.is_empty() {
            return None;
        }
        let idx = self
            .active
            .and_then(|id| self.tabs.iter().position(|t| t.id == id))
            .unwrap_or(0);
        let n = self.tabs.len();
        let next = if forward {
            (idx + 1) % n
        } else {
            (idx + n - 1) % n
        };
        let id = self.tabs[next].id;
        self.active = Some(id);
        Some(id)
    }

    /// Close a tab. If it was active, activate a neighbor. Returns true if closed.
    pub fn close(&mut self, id: TabId) -> bool {
        let Some(pos) = self.tabs.iter().position(|t| t.id == id) else {
            return false;
        };
        self.tabs.remove(pos);
        if self.active == Some(id) {
            self.active = if self.tabs.is_empty() {
                None
            } else {
                let new_pos = pos.min(self.tabs.len() - 1);
                Some(self.tabs[new_pos].id)
            };
        }
        true
    }

    /// Rename a tab title.
    pub fn rename(&mut self, id: TabId, title: impl Into<String>) -> bool {
        if let Some(t) = self.tabs.iter_mut().find(|t| t.id == id) {
            t.title = title.into();
            true
        } else {
            false
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn open_switch_close_multiple_shells() {
        let mut m = TabModel::new();
        assert!(m.is_empty());

        let a = m.open(TabKind::Shell, "shell-1");
        let b = m.open(TabKind::Shell, "shell-2");
        assert_eq!(m.len(), 2);
        assert_eq!(m.active_id(), Some(b));

        assert!(m.switch(a));
        assert_eq!(m.active_id(), Some(a));
        assert!(!m.switch(TabId(999)));

        assert!(m.close(a));
        assert_eq!(m.len(), 1);
        assert_eq!(m.active_id(), Some(b));
        assert!(m.close(b));
        assert!(m.is_empty());
        assert_eq!(m.active_id(), None);
    }

    #[test]
    fn cycle_and_list() {
        let mut m = TabModel::new();
        let a = m.open(TabKind::Shell, "a");
        let b = m.open(TabKind::Grok, "g");
        let c = m.open(TabKind::Shell, "c");
        assert_eq!(m.active_id(), Some(c));
        assert_eq!(m.cycle(true), Some(a));
        assert_eq!(m.cycle(false), Some(c));
        assert_eq!(m.list().len(), 3);
        assert_eq!(m.list()[1].kind, TabKind::Grok);
        assert_eq!(m.list()[1].id, b);
    }
}
