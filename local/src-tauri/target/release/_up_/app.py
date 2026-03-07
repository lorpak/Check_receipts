# Этот файл — точка входа CLI: читает аргументы, загружает payload и запускает обработку.
import argparse
import importlib.machinery
import importlib.util
import json
import os
import sys

# Ensure local package (check_receipts) is importable with embedded Python.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


def _find_cached_pyc(package_dir: str, module_name: str) -> str | None:
    cache_dir = os.path.join(package_dir, "__pycache__")
    if not os.path.isdir(cache_dir):
        return None
    prefix = f"{module_name}.cpython-"
    candidates = [n for n in os.listdir(cache_dir) if n.startswith(prefix) and n.endswith(".pyc")]
    if not candidates:
        return None
    cache_tag = getattr(sys.implementation, "cache_tag", "")
    preferred = f"{module_name}.{cache_tag}.pyc" if cache_tag else ""
    if preferred and preferred in candidates:
        return os.path.join(cache_dir, preferred)
    candidates.sort(reverse=True)
    return os.path.join(cache_dir, candidates[0])


def _load_pyc_module(module_name: str, pyc_path: str, is_package: bool = False, package_dir: str | None = None):
    loader = importlib.machinery.SourcelessFileLoader(module_name, pyc_path)
    spec = importlib.util.spec_from_loader(module_name, loader, is_package=is_package)
    if spec is None:
        raise ImportError(f"Не удалось создать spec для {module_name}")
    module = importlib.util.module_from_spec(spec)
    if is_package and package_dir:
        module.__path__ = [package_dir]
    sys.modules[module_name] = module
    loader.exec_module(module)
    return module


def _import_process_payload():
    try:
        from orchestration.payload_handler import process_payload as fn
        return fn
    except ModuleNotFoundError as exc:
        # Fallback for bundled _up_ where only __pycache__ (*.pyc) can be present.
        if not str(exc).startswith("No module named 'orchestration"):
            raise

    package_dir = os.path.join(BASE_DIR, "orchestration")
    init_pyc = _find_cached_pyc(package_dir, "__init__")
    if not init_pyc:
        raise ModuleNotFoundError("No module named 'orchestration.payload_handler'")

    _load_pyc_module("orchestration", init_pyc, is_package=True, package_dir=package_dir)
    for name in ("constants", "sources", "state", "runner", "payload_handler"):
        pyc_path = _find_cached_pyc(package_dir, name)
        if not pyc_path:
            raise ModuleNotFoundError(f"No module named 'orchestration.{name}'")
        _load_pyc_module(f"orchestration.{name}", pyc_path)

    from orchestration.payload_handler import process_payload as fn
    return fn


process_payload = _import_process_payload()


def main():
    parser = argparse.ArgumentParser(description="CheckReceipts Tauri backend")
    parser.add_argument("--payload-file", required=True, help="Путь к JSON с параметрами обработки")
    parser.add_argument("--status-file", required=True, help="Путь для записи статуса обработки")
    args = parser.parse_args()

    try:
        with open(args.payload_file, "r", encoding="utf-8") as f:
            payload = json.load(f)
        process_payload(payload, args.status_file)
    except Exception as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
