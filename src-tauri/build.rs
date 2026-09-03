use std::path::PathBuf;

fn assert_exists(path: &PathBuf, description: &str) {
  if !path.exists() {
    panic!(
      "Missing bundled Python resource: {} ({})",
      path.display(),
      description
    );
  }
}

fn main() {
  let manifest_dir = PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR is not set"));
  let python_dir = manifest_dir.join("..").join("resources").join("python");

  if std::env::var("PROFILE").as_deref() == Ok("release") {
    println!("cargo:rerun-if-changed={}", python_dir.display());

    assert_exists(&python_dir, "python runtime directory");
    assert_exists(&python_dir.join("python.exe"), "python executable");
    assert_exists(&python_dir.join("python313.dll"), "python shared library");
    assert_exists(
      &python_dir.join("Lib").join("site-packages"),
      "site-packages directory",
    );
  }

  tauri_build::build()
}
