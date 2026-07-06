// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

use std::env;

use anyhow::Result;
use ninja_gen::action::BuildAction;
use ninja_gen::build::BuildProfile;
use ninja_gen::build::FilesHandle;
use ninja_gen::cargo::CargoBuild;
use ninja_gen::cargo::CargoClippy;
use ninja_gen::cargo::CargoFormat;
use ninja_gen::cargo::CargoTest;
use ninja_gen::cargo::RustOutput;
use ninja_gen::git::SyncSubmodule;
use ninja_gen::glob;
use ninja_gen::hash::simple_hash;
use ninja_gen::input::BuildInput;
use ninja_gen::inputs;
use ninja_gen::Build;

use crate::platform::overriden_rust_target_triple;

pub fn build_rust(build: &mut Build) -> Result<()> {
    prepare_translations(build)?;
    build_proto_descriptors_and_interfaces(build)?;
    build_rsbridge(build)?;
    build_sync_server(build)
}

fn prepare_translations(build: &mut Build) -> Result<()> {
    let offline_build = env::var("OFFLINE_BUILD").is_ok();

    // ensure repos are checked out
    build.add_action(
        "ftl:repo:core",
        SyncSubmodule {
            path: "ftl/core-repo",
            offline_build,
        },
    )?;
    build.add_action(
        "ftl:repo:qt",
        SyncSubmodule {
            path: "ftl/qt-repo",
            offline_build,
        },
    )?;
    // build anki_i18n and spit out strings.json
    build.add_action(
        "rslib:i18n",
        CargoBuild {
            inputs: inputs![
                glob!["rslib/i18n/**"],
                glob!["ftl/{core,core-repo,qt,qt-repo}/**"],
                ":ftl:repo",
            ],
            outputs: &[
                RustOutput::Data("py", "pylib/anki/_fluent.py"),
                RustOutput::Data("ts", "ts/lib/generated/ftl.ts"),
            ],
            target: None,
            extra_args: "-p anki_i18n",
            release_override: None,
        },
    )?;

    build.add_action(
        "ftl:bin",
        CargoBuild {
            inputs: inputs![glob!["ftl/**"],],
            outputs: &[RustOutput::Binary("ftl")],
            target: None,
            extra_args: "-p ftl",
            release_override: None,
        },
    )?;

    // These don't use :group notation, as it doesn't make sense to invoke multiple
    // commands as a group.
    build.add_action(
        "ftl-sync",
        FtlCommand {
            args: "sync",
            deps: inputs![":ftl:repo", glob!["ftl/**"]],
        },
    )?;

    build.add_action(
        "ftl-deprecate",
        FtlCommand {
            args: "deprecate --ftl-roots ftl/core ftl/qt --source-roots pylib qt rslib ts --json-roots ftl/usage",
            deps: inputs!["ftl/core", "ftl/qt", "pylib", "qt", "rslib", "ts"],
        },
    )?;

    Ok(())
}

struct FtlCommand {
    args: &'static str,
    deps: BuildInput,
}

impl BuildAction for FtlCommand {
    fn command(&self) -> &str {
        "$ftl_bin $args"
    }

    fn files(&mut self, build: &mut impl FilesHandle) {
        build.add_inputs("", &self.deps);
        build.add_inputs("ftl_bin", inputs![":ftl:bin"]);
        build.add_variable("args", self.args);
        build.add_output_stamp(format!("ftl/stamp.{}", simple_hash(self.args)));
    }
}

fn build_proto_descriptors_and_interfaces(build: &mut Build) -> Result<()> {
    let outputs = vec![
        RustOutput::Data("descriptors.bin", "rslib/proto/descriptors.bin"),
        RustOutput::Data("py", "pylib/anki/_backend_generated.py"),
        RustOutput::Data("ts", "ts/lib/generated/backend.ts"),
    ];
    build.add_action(
        "rslib:proto",
        CargoBuild {
            inputs: inputs![glob!["{proto,rslib/proto}/**"], ":protoc_binary",],
            outputs: &outputs,
            target: None,
            extra_args: "-p anki_proto",
            release_override: None,
        },
    )?;
    Ok(())
}

fn build_rsbridge(build: &mut Build) -> Result<()> {
    let features = if cfg!(target_os = "linux") {
        "rustls"
    } else {
        "native-tls"
    };
    build.add_action(
        "pylib:rsbridge",
        CargoBuild {
            inputs: inputs![
                glob!["{pylib/rsbridge/**,rslib/**}"],
                // declare a dependency on i18n/proto so they get built first, allowing
                // things depending on them to build faster, and ensuring
                // changes to the ftl files trigger a rebuild
                ":rslib:i18n",
                ":rslib:proto",
                // when env vars change the build hash gets updated
                "$builddir/env",
                "$builddir/buildhash",
                // building on Windows requires python3.lib
                if cfg!(windows) {
                    inputs![":pyenv:bin"]
                } else {
                    inputs![]
                }
            ],
            outputs: &[RustOutput::DynamicLib("rsbridge")],
            target: overriden_rust_target_triple(),
            extra_args: &format!("-p rsbridge --features {features}"),
            release_override: None,
        },
    )
}

/// Copy the built sync-server binary into out/bin. On Apple Silicon, cargo/ld
/// emit a *linker-signed* ad-hoc signature that the kernel rejects the moment
/// the binary is copied to a new path (it SIGKILLs it with no output), so the
/// copy is re-signed ad-hoc; a plain copy is sufficient elsewhere.
struct StageSyncServer {
    input: BuildInput,
    output: &'static str,
}

impl BuildAction for StageSyncServer {
    fn command(&self) -> &str {
        if cfg!(target_os = "macos") {
            "cp -f $in $out && codesign --force --sign - $out"
        } else {
            "cp -f $in $out"
        }
    }

    fn files(&mut self, build: &mut impl FilesHandle) {
        build.add_inputs("in", &self.input);
        build.add_outputs("out", vec![self.output]);
    }
}

/// Build the standalone `anki-sync-server` binary and stage it into `out/bin`,
/// where the desktop's Speedrun phone-sync feature
/// (`speedrun_sync._binary_path`) looks for it. Wiring this into the build
/// graph keeps the server in lockstep with the collection wire format - a
/// manually built binary silently goes stale whenever the sync/storage code
/// changes, which breaks phone sync.
fn build_sync_server(build: &mut Build) -> Result<()> {
    build.add_action(
        "rslib:sync-server:bin",
        CargoBuild {
            inputs: inputs![
                glob!["rslib/**"],
                // the server links the anki crate, which needs generated i18n/proto
                ":rslib:i18n",
                ":rslib:proto",
                "$builddir/env",
                "$builddir/buildhash",
            ],
            outputs: &[RustOutput::Binary("anki-sync-server")],
            target: None,
            extra_args: "-p anki-sync-server",
            release_override: None,
        },
    )?;
    // Stage into out/bin so the runtime lookup finds it regardless of profile.
    build.add_action(
        "anki:sync-server",
        StageSyncServer {
            input: inputs![":rslib:sync-server:bin"],
            output: "bin/anki-sync-server",
        },
    )
}

pub fn check_rust(build: &mut Build) -> Result<()> {
    let inputs = inputs![
        glob!("{rslib/**,pylib/rsbridge/**,ftl/**,build/**,tools/minilints/**}"),
        "Cargo.lock",
        "Cargo.toml",
        "rust-toolchain.toml",
    ];
    build.add_action(
        "check:format:rust",
        CargoFormat {
            inputs: inputs.clone(),
            check_only: true,
            working_dir: Some("cargo/format"),
        },
    )?;
    build.add_action(
        "format:rust",
        CargoFormat {
            inputs: inputs.clone(),
            check_only: false,
            working_dir: Some("cargo/format"),
        },
    )?;

    let inputs = inputs![
        inputs,
        // defer tests until build has completed; ensure re-run on changes
        ":pylib:rsbridge"
    ];

    build.add_action(
        "check:clippy",
        CargoClippy {
            inputs: inputs.clone(),
        },
    )?;
    build.add_action("check:rust_test", CargoTest { inputs })?;

    Ok(())
}

pub fn check_minilints(build: &mut Build) -> Result<()> {
    struct RunMinilints {
        pub deps: BuildInput,
        pub fix: bool,
    }

    impl BuildAction for RunMinilints {
        fn command(&self) -> &str {
            "$minilints_bin $fix $stamp"
        }

        fn bypass_runner(&self) -> bool {
            true
        }

        fn files(&mut self, build: &mut impl FilesHandle) {
            build.add_inputs("minilints_bin", inputs![":build:minilints"]);
            build.add_inputs("", &self.deps);
            build.add_variable("fix", if self.fix { "fix" } else { "check" });
            build.add_output_stamp(format!("tests/minilints.{}", self.fix));
        }

        fn on_first_instance(&self, build: &mut Build) -> Result<()> {
            build.add_action(
                "build:minilints",
                CargoBuild {
                    inputs: inputs![glob!("tools/minilints/**/*")],
                    outputs: &[RustOutput::Binary("minilints")],
                    target: None,
                    extra_args: "-p minilints",
                    release_override: Some(BuildProfile::Debug),
                },
            )
        }
    }

    let files = inputs![
        glob![
            "**/*.{py,rs,ts,svelte,mjs,md}",
            "{target,extra,.mypy_cache,node_modules,ts/.svelte-kit}/**"
        ],
        "Cargo.lock"
    ];

    build.add_action(
        "check:minilints",
        RunMinilints {
            deps: files.clone(),
            fix: false,
        },
    )?;
    build.add_action(
        "fix:minilints",
        RunMinilints {
            deps: files,
            fix: true,
        },
    )?;
    Ok(())
}
