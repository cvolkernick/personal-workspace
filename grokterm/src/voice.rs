//! Two-way voice path: intents/tools → same manager control-plane actions.

use crate::manager::{Manager, ManagerCommand, ManagerResponse};
use crate::tab::TabId;
use serde::{Deserialize, Serialize};

/// Voice / tool intent payloads (JSON-serializable for future live voice bridge).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "intent", rename_all = "snake_case")]
pub enum VoiceIntent {
    OpenShell {
        #[serde(default)]
        title: Option<String>,
    },
    OpenGrok {
        #[serde(default)]
        title: Option<String>,
        #[serde(default)]
        args: Vec<String>,
    },
    CloseTab {
        #[serde(default)]
        id: Option<u64>,
    },
    ListTabs,
    SwitchTab {
        id: u64,
    },
    ManagerHelp,
    /// Settings-class / delegate placeholder mapped to help for MVP.
    Delegate {
        #[serde(default)]
        target: Option<String>,
        #[serde(default)]
        message: Option<String>,
    },
    Settings {
        #[serde(default)]
        key: Option<String>,
        #[serde(default)]
        value: Option<String>,
    },
    Quit,
}

/// Map a voice intent onto the shared manager command set.
pub fn intent_to_command(intent: VoiceIntent) -> ManagerCommand {
    match intent {
        VoiceIntent::OpenShell { title } => ManagerCommand::OpenShell { title },
        VoiceIntent::OpenGrok { title, args } => ManagerCommand::OpenGrok { title, args },
        VoiceIntent::CloseTab { id } => ManagerCommand::Close {
            id: id.map(TabId),
        },
        VoiceIntent::ListTabs => ManagerCommand::List,
        VoiceIntent::SwitchTab { id } => ManagerCommand::Switch { id: TabId(id) },
        VoiceIntent::ManagerHelp => ManagerCommand::Help,
        // Delegate / settings map to control-plane ops that exist today.
        // Delegate → list (surface tabs for delegation); settings → help.
        VoiceIntent::Delegate { .. } => ManagerCommand::List,
        VoiceIntent::Settings { .. } => ManagerCommand::Help,
        VoiceIntent::Quit => ManagerCommand::Quit,
    }
}

/// Dispatch a voice intent through the real manager (same path as typed commands).
pub fn dispatch_voice_intent(manager: &mut Manager, intent: VoiceIntent) -> ManagerResponse {
    let cmd = intent_to_command(intent);
    manager.apply(cmd)
}

/// Parse JSON tool payload into an intent (for future live voice bridge).
pub fn parse_voice_json(json: &str) -> Result<VoiceIntent, String> {
    serde_json::from_str(json).map_err(|e| format!("invalid voice intent JSON: {e}"))
}

/// CLI/entry documentation for `--voice`.
pub fn voice_entry_help() -> &'static str {
    "Voice entry path: maps two-way Grok Voice tool intents to manager control-plane \
     actions (open shell, open grok, close tab, list/switch, help, quit). \
     Live mic/speaker requires Grok Voice APIs and audio devices; pure dispatch is always available."
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::manager::{GrokOpenResult, ManagerResponse};
    use crate::tab::TabKind;

    #[test]
    fn voice_open_shell_uses_same_control_plane() {
        let mut m = Manager::new();
        let r = dispatch_voice_intent(
            &mut m,
            VoiceIntent::OpenShell {
                title: Some("voice-shell".into()),
            },
        );
        match r {
            ManagerResponse::Opened {
                kind: TabKind::Shell,
                title,
                ..
            } => assert_eq!(title, "voice-shell"),
            other => panic!("unexpected {other:?}"),
        }
        assert_eq!(m.tabs.len(), 1);
    }

    #[test]
    fn voice_open_grok_close_list_help() {
        let mut m = Manager::new();
        let open = dispatch_voice_intent(
            &mut m,
            VoiceIntent::OpenGrok {
                title: Some("v-grok".into()),
                args: vec![],
            },
        );
        let id = match open {
            ManagerResponse::Opened {
                id,
                kind: TabKind::Grok,
                grok: Some(_),
                ..
            } => id,
            // Ready or Missing both open a tab by default
            ManagerResponse::Opened { id, kind, .. } => {
                assert_eq!(kind, TabKind::Grok);
                id
            }
            other => panic!("unexpected {other:?}"),
        };

        let listed = dispatch_voice_intent(&mut m, VoiceIntent::ListTabs);
        assert!(matches!(listed, ManagerResponse::Listed { .. }));

        let help = dispatch_voice_intent(&mut m, VoiceIntent::ManagerHelp);
        assert!(matches!(help, ManagerResponse::HelpText(t) if t.contains("GrokTerm manager")));

        let closed = dispatch_voice_intent(
            &mut m,
            VoiceIntent::CloseTab {
                id: Some(id.0),
            },
        );
        assert!(matches!(
            closed,
            ManagerResponse::Closed {
                remaining: 0,
                ..
            }
        ));
    }

    #[test]
    fn voice_json_roundtrip_and_delegate_settings() {
        let json = r#"{"intent":"open_shell","title":"from-json"}"#;
        let intent = parse_voice_json(json).unwrap();
        assert_eq!(
            intent,
            VoiceIntent::OpenShell {
                title: Some("from-json".into())
            }
        );

        let mut m = Manager::new();
        m.apply(ManagerCommand::OpenShell { title: None });
        let r = dispatch_voice_intent(
            &mut m,
            VoiceIntent::Delegate {
                target: Some("shell-1".into()),
                message: Some("run tests".into()),
            },
        );
        assert!(matches!(r, ManagerResponse::Listed { .. }));

        let r = dispatch_voice_intent(
            &mut m,
            VoiceIntent::Settings {
                key: Some("theme".into()),
                value: Some("dark".into()),
            },
        );
        assert!(matches!(r, ManagerResponse::HelpText(_)));
    }

    #[test]
    fn intent_to_command_matches_manager_parse_surface() {
        // Ensure voice maps to the same command enum the manager applies.
        assert_eq!(
            intent_to_command(VoiceIntent::OpenShell { title: None }),
            ManagerCommand::OpenShell { title: None }
        );
        assert_eq!(
            intent_to_command(VoiceIntent::ManagerHelp),
            ManagerCommand::Help
        );
        assert_eq!(
            intent_to_command(VoiceIntent::Quit),
            ManagerCommand::Quit
        );
        let _ = GrokOpenResult::Missing {
            message: "x".into(),
        };
    }
}
