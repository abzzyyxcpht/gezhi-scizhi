"""Build the Windows installer in an owned temporary ASCII directory."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from src.version import APP_VERSION as APP_VER  # noqa: E402 -- direct script entry adds the repository root

REQUIRED_SELF_CHECKS = frozenset({'imports', 'version', 'data_isolation', 'runtime_plan',
    'swarm_plan_cancel', 'frozen_desktop_client', 'session_event_projection',
    'session_status_compact', 'network_isolation', 'product_readiness', 'product_demo_offline', 'pdf_backend',
    'experimental_defaults', 'experimental_queue', 'experimental_steering', 'evidence_review_inbox', 'history_index',
    'composer_drafts', 'tool_outputs', 'shell_owned_execution', 'shell_stream_capture', 'interaction_controls'})


def _validated_check(path: Path) -> dict:
    try:
        report = json.loads(path.read_text(encoding='utf-8'))
        checks = report.get('checks') or []
        passed = {item['name'] for item in checks if item.get('ok') is True}
        if (report.get('ok') is not True or report.get('frozen') is not True
                or report.get('version') != APP_VER or not REQUIRED_SELF_CHECKS <= passed
                or any(item.get('ok') is not True for item in checks)):
            raise ValueError('missing, stale or failed verification')
        return report
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise SystemExit(f'Frozen self-check report is invalid: {exc}') from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-dir', type=Path, help='Version-scoped PyInstaller application directory')
    args = parser.parse_args([] if argv is None else argv)
    app = args.app_dir or ROOT / 'dist' / APP_VER / 'Gezhi'
    app = app.resolve()
    output = ROOT / 'dist' / f'SciZhi-Agent-Setup-{APP_VER}.exe'
    if output.exists():
        raise FileExistsError(f'Refusing to overwrite existing installer: {output}')
    if not (app / "Gezhi.exe").is_file():
        raise SystemExit(f'Missing {app / "Gezhi.exe"}; build Gezhi.spec first')
    bundled = ROOT / 'tools' / 'nsis-3.10' / 'Bin' / 'makensis.exe'
    if not bundled.is_file():
        bundled = ROOT / 'tools' / 'nsis-3.10' / 'makensis.exe'
    makensis = str(bundled) if bundled.is_file() else shutil.which('makensis')
    if not makensis or not Path(makensis).is_file():
        raise SystemExit("Install NSIS 3 and add makensis to PATH")
    with tempfile.TemporaryDirectory(prefix="scizhi-nsis-") as owned:
        stage = Path(owned)
        if not str(stage).isascii():
            raise SystemExit('NSIS requires an ASCII temporary directory; configure TEMP before packaging')
        shutil.copytree(app, stage / "app")
        shutil.copy2(ROOT / "assets" / "gezhi.ico", stage / "gezhi.ico")
        shutil.copy2(ROOT / "packaging" / "gezhi.nsi", stage / "gezhi.nsi")
        report_path = stage / 'frozen-self-check.json'
        environment = {key: value for key, value in os.environ.items() if not key.startswith('GEZHI_LLM_')}
        environment['GEZHI_DATA_DIR'] = str(stage / 'self-check-data')
        check = subprocess.run([str(stage / 'app' / 'Gezhi.exe'), '--self-check', str(report_path)],
            cwd=stage, env=environment, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120)
        if check.returncode:
            raise SystemExit(f'Frozen self-check failed (exit {check.returncode})')
        report = _validated_check(report_path)
        result = subprocess.run([makensis, "-WX", "-INPUTCHARSET", "UTF8", f'-DAPP_VER={APP_VER}', "gezhi.nsi"],
            cwd=stage, capture_output=True, text=True, encoding='utf-8', errors='replace')
        if result.returncode:
            print(result.stdout, file=sys.stderr)
            return result.returncode
        output.parent.mkdir(parents=True, exist_ok=True)
        generated = stage / output.name
        if not generated.is_file() or generated.stat().st_size == 0:
            raise SystemExit('NSIS did not create the requested installer')
        with generated.open('rb') as source, output.open('xb') as target:
            shutil.copyfileobj(source, target)
        digest = hashlib.sha256(output.read_bytes()).hexdigest()
        output.with_suffix('.exe.sha256').write_text(f'{digest}  {output.name}\n', encoding='ascii')
        output.with_suffix('.self-check.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'version': APP_VER, 'installer': str(output), 'bytes': output.stat().st_size,
                          'sha256': digest, 'frozen_self_check': True}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
