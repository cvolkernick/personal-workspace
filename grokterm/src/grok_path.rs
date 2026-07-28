//! Resolve the local Grok Build CLI binary path.

use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use thiserror::Error;

/// Successful resolution of the Grok CLI.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct GrokBinary {
    pub path: PathBuf,
    pub source: GrokSource,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum GrokSource {
    /// Found at `~/.grok/bin/grok` (preferred).
    GrokHome,
    /// Found on PATH.
    PathEnv,
    /// Explicit override (env or caller-supplied).
    Override,
}

#[derive(Debug, Error, Clone, PartialEq, Eq)]
pub enum GrokPathError {
    #[error(
        "Grok Build CLI not found. Install Grok Build, or place the binary at ~/.grok/bin/grok \
         (or ensure `grok` is on PATH). Grok tabs require the real CLI."
    )]
    NotFound,
}

/// Default preferred location under the user's home.
pub fn default_grok_home_path() -> PathBuf {
    dirs_home()
        .map(|h| h.join(".grok").join("bin").join("grok"))
        .unwrap_or_else(|| PathBuf::from("~/.grok/bin/grok"))
}

fn dirs_home() -> Option<PathBuf> {
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
}

/// Resolve Grok binary.
///
/// Order:
/// 1. `override_path` if provided and exists
/// 2. `GROKTERM_GROK` env if set and exists
/// 3. `~/.grok/bin/grok` if exists
/// 4. `which grok` / PATH search
pub fn resolve_grok_binary(override_path: Option<&Path>) -> Result<GrokBinary, GrokPathError> {
    // Explicit override is authoritative: if set and not a file, do not fall through.
    if let Some(p) = override_path {
        if p.is_file() {
            return Ok(GrokBinary {
                path: p.to_path_buf(),
                source: GrokSource::Override,
            });
        }
        return Err(GrokPathError::NotFound);
    }

    if let Ok(env_p) = std::env::var("GROKTERM_GROK") {
        let p = PathBuf::from(env_p);
        if p.is_file() {
            return Ok(GrokBinary {
                path: p,
                source: GrokSource::Override,
            });
        }
    }

    let home_path = default_grok_home_path();
    if home_path.is_file() {
        return Ok(GrokBinary {
            path: home_path,
            source: GrokSource::GrokHome,
        });
    }

    if let Ok(p) = which::which("grok") {
        return Ok(GrokBinary {
            path: p,
            source: GrokSource::PathEnv,
        });
    }

    Err(GrokPathError::NotFound)
}

/// Build argv for opening a Grok tab (path + optional extra args).
pub fn grok_spawn_argv(
    binary: &GrokBinary,
    extra: &[String],
) -> (PathBuf, Vec<String>) {
    (binary.path.clone(), extra.to_vec())
}

/// Human-readable degraded error when Grok is missing (for manager / host UI).
pub fn missing_grok_message() -> String {
    GrokPathError::NotFound.to_string()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::tempdir;

    #[test]
    fn override_path_wins_when_file_exists() {
        let dir = tempdir().unwrap();
        let fake = dir.path().join("fake-grok");
        fs::write(&fake, b"#!/bin/sh\necho ok\n").unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mut perms = fs::metadata(&fake).unwrap().permissions();
            perms.set_mode(0o755);
            fs::set_permissions(&fake, perms).unwrap();
        }

        let got = resolve_grok_binary(Some(&fake)).expect("override should resolve");
        assert_eq!(got.path, fake);
        assert_eq!(got.source, GrokSource::Override);

        let (path, args) = grok_spawn_argv(&got, &["--version".into()]);
        assert_eq!(path, fake);
        assert_eq!(args, vec!["--version".to_string()]);
    }

    #[test]
    fn missing_path_returns_clear_error() {
        let dir = tempdir().unwrap();
        let missing = dir.path().join("no-such-grok");
        let msg = missing_grok_message();
        assert!(msg.contains("Grok Build CLI not found"), "{msg}");
        assert!(msg.contains("~/.grok/bin/grok"), "{msg}");

        // Explicit override that is not a file is NotFound (no PATH fallthrough).
        let err = resolve_grok_binary(Some(&missing)).unwrap_err();
        assert_eq!(err, GrokPathError::NotFound);
    }

    #[test]
    fn resolves_real_grok_when_present() {
        let home = default_grok_home_path();
        match resolve_grok_binary(None) {
            Ok(b) => {
                assert!(b.path.is_file(), "resolved path should exist: {:?}", b.path);
                // Prefer home path when present.
                if home.is_file() {
                    assert_eq!(b.source, GrokSource::GrokHome);
                    assert_eq!(b.path, home);
                }
            }
            Err(GrokPathError::NotFound) => {
                // Acceptable on machines without Grok installed.
                assert!(!home.is_file() || which::which("grok").is_err());
            }
        }
    }
}
