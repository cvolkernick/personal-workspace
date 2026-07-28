//! GrokTerm CLI — multi-tab PTY host for Grok Build.

use clap::{Parser, Subcommand};
use grokterm::host::{run_manager_line, run_voice_demo, Host};
use grokterm::keys::bindings_help;
use grokterm::manager::Manager;
use grokterm::voice::voice_entry_help;
use grokterm::{PRODUCT_DESCRIPTION, PRODUCT_NAME, PRODUCT_VERSION};
use std::io::Write;
use std::path::PathBuf;
use std::process::ExitCode;

#[derive(Parser, Debug)]
#[command(
    name = "grokterm",
    version = PRODUCT_VERSION,
    about = PRODUCT_DESCRIPTION,
    long_about = concat!(
        "GrokTerm — multi-tab terminal host for Grok Build.\n\n",
        "Real PTY shell and Grok sessions, manager control plane, and two-way voice tools.\n\n",
        "Keys: Ctrl+T new shell · Ctrl+B new Grok · Ctrl+G manager · Ctrl+V voice · Ctrl+Q quit\n\n",
        "Entry points:\n",
        "  grokterm              Interactive multi-tab host\n",
        "  grokterm --grok       Start with a Grok Build tab\n",
        "  grokterm --voice      Voice entry path (dispatch + live if available)\n",
        "  grokterm manager …    Run a manager command without the full TUI\n"
    )
)]
struct Cli {
    /// Start with a Grok Build tab (requires local grok CLI).
    #[arg(long)]
    grok: bool,

    /// Voice entry path: enable voice overlay / dispatch tools to the manager.
    #[arg(long)]
    voice: bool,

    /// Override path to the Grok Build CLI binary.
    #[arg(long, value_name = "PATH")]
    grok_bin: Option<PathBuf>,

    /// Print host key bindings and exit.
    #[arg(long)]
    keys: bool,

    /// Non-interactive: open a shell PTY, echo a nonce, print result, exit (PTY smoke).
    #[arg(long, hide = true)]
    pty_smoke: bool,

    #[command(subcommand)]
    command: Option<Commands>,
}

#[derive(Subcommand, Debug)]
enum Commands {
    /// Run a manager control-plane command (help, shell, grok, list, …).
    Manager {
        /// Manager command words (default: help).
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Resolve Grok CLI path and print spawn argv.
    GrokPath,
    /// Voice dispatch demo (no mic) — exercises the same control plane.
    VoiceDemo,
}

fn main() -> ExitCode {
    let cli = Cli::parse();

    if cli.keys {
        println!("{PRODUCT_NAME} v{PRODUCT_VERSION}");
        println!("{}", bindings_help());
        return ExitCode::SUCCESS;
    }

    if cli.pty_smoke {
        return match run_pty_smoke() {
            Ok(()) => ExitCode::SUCCESS,
            Err(e) => {
                eprintln!("pty-smoke failed: {e:#}");
                ExitCode::FAILURE
            }
        };
    }

    if let Some(cmd) = cli.command {
        return match run_subcommand(cmd, cli.grok_bin.as_ref()) {
            Ok(()) => ExitCode::SUCCESS,
            Err(e) => {
                eprintln!("error: {e}");
                ExitCode::FAILURE
            }
        };
    }

    // Interactive host
    let mut host = Host::new().with_grok_override(cli.grok_bin.clone());
    if let Err(e) = host.bootstrap(cli.grok) {
        eprintln!("{PRODUCT_NAME}: bootstrap failed: {e:#}");
        return ExitCode::FAILURE;
    }

    if cli.voice {
        // Enter voice path: print entry help, then TUI with voice overlay intent.
        eprintln!("{PRODUCT_NAME} voice: {}", voice_entry_help());
        // Non-TTY: run voice demo only
        if !std::io::IsTerminal::is_terminal(&std::io::stdin()) {
            let mut m = Manager::new();
            m.grok_override = cli.grok_bin;
            for r in run_voice_demo(&mut m) {
                println!("{r:?}");
            }
            return ExitCode::SUCCESS;
        }
    }

    // If not a TTY, don't enter raw mode TUI
    if !std::io::IsTerminal::is_terminal(&std::io::stdin()) {
        println!("{PRODUCT_NAME} v{PRODUCT_VERSION}");
        println!("{PRODUCT_DESCRIPTION}");
        println!("Non-interactive stdin; use --help, manager, or a TTY for the host UI.");
        println!("{}", bindings_help());
        return ExitCode::SUCCESS;
    }

    match host.run_tui() {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("{PRODUCT_NAME} exited with error: {e:#}");
            ExitCode::FAILURE
        }
    }
}

fn run_subcommand(cmd: Commands, grok_bin: Option<&PathBuf>) -> anyhow::Result<()> {
    match cmd {
        Commands::Manager { args } => {
            let line = if args.is_empty() {
                "help".to_string()
            } else {
                args.join(" ")
            };
            let mut m = Manager::new();
            m.grok_override = grok_bin.cloned();
            let resp = run_manager_line(&mut m, &line).map_err(anyhow::Error::msg)?;
            println!("{resp:#?}");
            // Also print human help text
            if let grokterm::ManagerResponse::HelpText(t) = &resp {
                println!("\n{t}");
            }
            Ok(())
        }
        Commands::GrokPath => {
            match grokterm::resolve_grok_binary(grok_bin.map(|p| p.as_path())) {
                Ok(b) => {
                    println!("path={}", b.path.display());
                    println!("source={:?}", b.source);
                    let (path, args) = grokterm::grok_path::grok_spawn_argv(&b, &[]);
                    println!("spawn_argv0={}", path.display());
                    println!("spawn_args={args:?}");
                }
                Err(e) => {
                    eprintln!("{e}");
                    return Err(anyhow::anyhow!(e.to_string()));
                }
            }
            Ok(())
        }
        Commands::VoiceDemo => {
            let mut m = Manager::new();
            m.grok_override = grok_bin.cloned();
            println!("{}", voice_entry_help());
            for r in run_voice_demo(&mut m) {
                println!("{r:?}");
            }
            Ok(())
        }
    }
}

fn run_pty_smoke() -> anyhow::Result<()> {
    use std::time::{SystemTime, UNIX_EPOCH};
    let nonce = format!(
        "GROKTERM_CLI_SMOKE_{}",
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    );
    let out = grokterm::pty_session::pty_echo_nonce(&nonce)?;
    let mut stdout = std::io::stdout();
    writeln!(stdout, "pty-smoke ok nonce={nonce}")?;
    writeln!(stdout, "output_contains_nonce={}", out.contains(&nonce))?;
    if !out.contains(&nonce) {
        anyhow::bail!("nonce missing from PTY output");
    }
    Ok(())
}
