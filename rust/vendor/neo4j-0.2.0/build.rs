// Copyright Rouven Bauer
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//    https://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

use std::env;
use std::process::Command;

fn get_rustc_version() -> String {
    let rustc = env::var("RUSTC").unwrap();

    let rust_version = Command::new(&rustc)
        .arg("--version")
        .output()
        .expect("Failed to invoke `rustc --version`");

    if rust_version.status.code().unwrap_or(1) != 0 {
        panic!(
            "`{} --version` {}\n\n--- stdout\n{}\n--- stderr{}",
            &rustc,
            match rust_version.status.code() {
                Some(code) => format!("exited with status code {}", code),
                None => String::from("was killed killed by signal"),
            },
            String::from_utf8_lossy(rust_version.stdout.as_slice()),
            String::from_utf8_lossy(rust_version.stderr.as_slice())
        );
    }

    let output = String::from_utf8_lossy(rust_version.stdout.as_slice());
    output.trim_start_matches("rustc ").trim().into()
}

fn main() {
    let rustc_version = get_rustc_version();

    let product = format!(
        "neo4j-rust-robsdedude/{}",
        env::var("CARGO_PKG_VERSION").unwrap()
    );
    println!("Setting bolt agent product to: {}", product);

    let platform = env::var("TARGET").unwrap();
    println!("Setting bolt agent platform to: {}", platform);

    let language = format!("Rust/{}", rustc_version);
    println!("Setting bolt agent language to: {}", language);

    let language_details = format!(
        "profile: {}; host: {}",
        env::var("PROFILE").unwrap(),
        env::var("HOST").unwrap()
    );
    println!(
        "Setting bolt agent language details to: {}",
        language_details
    );

    let user_agent = format!("{} {} {}", product, language, platform);
    println!("Setting default user agent to: {}", user_agent);

    println!("cargo:rustc-env=NEO4J_DEFAULT_USER_AGENT={}", user_agent);
    println!("cargo:rustc-env=NEO4J_BOLT_AGENT_PRODUCT={}", product);
    println!("cargo:rustc-env=NEO4J_BOLT_AGENT_PLATFORM={}", platform);
    println!("cargo:rustc-env=NEO4J_BOLT_AGENT_LANGUAGE={}", language);
    println!(
        "cargo:rustc-env=NEO4J_BOLT_AGENT_LANGUAGE_DETAILS={}",
        language_details
    );
}
