"""Build/install/rollback the SeaDAS Color Manager RGB lifetime/order patch.

Python standard library only. Build needs a JDK >=21 and ASM from SeaDAS.
Install and rollback are supported on Windows; other systems require review.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
import zipfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
MODULE = Path('snap/modules/org-esa-snap-snap-rcp.jar')
ENTRY = 'org/esa/snap/rcp/colormanip/ColorManipulationFormImpl.class'
KNOWN_ORIGINAL = '55662fd780ad09e0b7756422c3799379d1d783e5af27d109e24c5a958f45dc2d'
KNOWN_PATCHED = 'e8c62196f85e1421d366dc47cc562f5f4916257cd77a6dae67b3ea2fa4bd7213'
SCHEMA = 1


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def run(command):
    result = subprocess.run([str(x) for x in command], text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(f'Command failed ({Path(command[0]).name}):\n{result.stdout}\n{result.stderr}')
    return result.stdout.strip()


def exe(jdk, name):
    path = Path(jdk) / 'bin' / (name + ('.exe' if os.name == 'nt' else ''))
    if not path.is_file():
        raise ValueError(f'Missing {path}. A full JDK is required; a JRE is not sufficient.')
    return path


def jdk_tools(jdk):
    tools = {key: exe(jdk, key) for key in ['java', 'javac', 'javap']}
    version = run([tools['javac'], '-version'])
    match = re.search(r'javac (\d+)', version)
    if not match or int(match[1]) < 21:
        raise ValueError('JDK 21 or later is required.')
    return tools


def module_version(jar):
    with zipfile.ZipFile(jar) as archive:
        manifest = archive.read('META-INF/MANIFEST.MF').decode('utf-8', errors='replace')
    match = re.search(r'OpenIDE-Module-Implementation-Version:\s*([^\r\n]+)', manifest)
    return match[1].strip() if match else 'unknown'


def assert_closed(home):
    if os.name != 'nt':
        raise ValueError('Automatic install/rollback is tested on Windows only. Build/status can be used elsewhere.')
    powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    code = "$ErrorActionPreference='Stop'; @(Get-CimInstance Win32_Process | Select-Object ProcessId,Name,ExecutablePath) | ConvertTo-Json -Compress"
    processes = json.loads(run([powershell, '-NoProfile', '-NonInteractive', '-Command', code]) or '[]')
    if isinstance(processes, dict):
        processes = [processes]
    root = str(Path(home).resolve()).casefold().rstrip('\\/')
    for proc in processes:
        name = (proc.get('Name') or '').casefold()
        location = (proc.get('ExecutablePath') or '').casefold()
        within = location.startswith(root + '\\') or location.startswith(root + '/')
        uncertain_launcher = not location and re.match(r'^(seadas|snap).*\.exe$', name)
        if within or uncertain_launcher:
            raise ValueError(f'SeaDAS may still be running (PID {proc["ProcessId"]}). Exit normally before changing modules/cache.')


def refresh_cache(cache_dir):
    # Only the rebuildable class-resource cache is moved; profiles/settings are preserved.
    cache = Path(cache_dir).resolve() / 'all-resources.dat'
    if not cache.is_file():
        return None
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = cache.with_name(cache.name + '.before-color-manager-fix-' + stamp + '-' + uuid.uuid4().hex[:6])
    cache.rename(backup)
    return str(backup)


def default_cache():
    if os.name != 'nt' or not os.environ.get('APPDATA'):
        return None
    return Path(os.environ['APPDATA']) / '.seadas/var/cache'


def atomic_copy(source, target):
    source, target = Path(source), Path(target)
    staged = target.with_name(target.name + '.color-manager-fix.tmp')
    if staged.exists():
        raise ValueError(f'Staging file already exists: {staged}; inspect it first.')
    with source.open('rb') as src, staged.open('xb') as dst:
        shutil.copyfileobj(src, dst)
        dst.flush()
        os.fsync(dst.fileno())
    if sha(source) != sha(staged):
        raise RuntimeError('Staged file checksum mismatch; destination was not replaced.')
    os.replace(staged, target)


def load_manifest(package):
    manifest = json.loads((Path(package) / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('schema') != SCHEMA or manifest.get('module') != MODULE.as_posix() or manifest.get('patch_version') != 2:
        raise ValueError('Unsupported or unexpected patch manifest.')
    for key in ['original_sha256', 'patched_sha256']:
        if not re.fullmatch(r'[0-9a-f]{64}', manifest.get(key, '')):
            raise ValueError('Invalid checksum in patch manifest.')
    return manifest


def build(args):
    home, output = args.seadas_home.resolve(), args.output.resolve()
    source = (args.input_jar or home / MODULE).resolve()
    tools = jdk_tools(args.jdk_home)
    source_sha = sha(source)
    known = source_sha == KNOWN_ORIGINAL
    if not known and not args.allow_unverified_version:
        raise ValueError('This original JAR is not the locally validated SeaDAS 12 build. Review the new version, then use --allow-unverified-version to generate a candidate; do not copy an old patched JAR over a new version.')
    if output.exists() and any(output.iterdir()):
        raise ValueError('Build output directory must be empty; choose a new --output for each SeaDAS build.')
    output.mkdir(parents=True, exist_ok=True)
    core = home / 'platform/core'
    # asm.jar plus matching asm-tree.jar, without downloading extra dependencies.
    candidates = [p for p in core.glob('asm-*.jar') if re.fullmatch(r'asm-\d[\d.]*\.jar', p.name)]
    candidates.sort(key=lambda p: tuple(int(x) for x in re.findall(r'\d+', p.stem)), reverse=True)
    asm = next((p for p in candidates if p.with_name(p.stem.replace('asm-', 'asm-tree-') + '.jar').is_file()), None)
    if asm is None:
        raise ValueError('Matching ASM and ASM-tree JARs were not found in SeaDAS platform/core.')
    tree = asm.with_name(asm.stem.replace('asm-', 'asm-tree-') + '.jar')
    classpath = os.pathsep.join([str(asm), str(tree)])
    with zipfile.ZipFile(source) as archive:
        names = archive.namelist()
        if len(set(names)) != len(names):
            raise ValueError('Duplicate ZIP entries require manual review.')
        signatures = [n for n in names if n.upper().startswith('META-INF/') and n.upper().endswith(('.SF', '.RSA', '.DSA', '.EC'))]
        if signatures:
            raise ValueError('This JAR is signed. Do not remove signatures; review a supported source build instead.')
        (output / 'original.class').write_bytes(archive.read(ENTRY))
    classes = output / 'classes'
    run([tools['javac'], '--release', '21', '-cp', classpath, '-d', classes,
         ROOT / 'src/PatchColorManager.java', ROOT / 'src/PatchInitializationOrder.java'])
    java_cp = os.pathsep.join([str(classes), classpath])
    run([tools['java'], '-Xverify:all', '-cp', java_cp, 'PatchColorManager', output / 'original.class', output / 'guard.class'])
    run([tools['java'], '-Xverify:all', '-cp', java_cp, 'PatchInitializationOrder', output / 'guard.class', output / 'patched.class'])
    patched_jar = output / 'patched.jar'
    with zipfile.ZipFile(source) as before, zipfile.ZipFile(patched_jar, 'w') as after:
        for item in before.infolist():
            after.writestr(item, (output / 'patched.class').read_bytes() if item.filename == ENTRY else before.read(item.filename))
    with zipfile.ZipFile(source) as before, zipfile.ZipFile(patched_jar) as after:
        if before.namelist() != after.namelist():
            raise RuntimeError('JAR entries changed unexpectedly.')
        changes = [n for n in before.namelist() if before.read(n) != after.read(n)]
        if changes != [ENTRY]:
            raise RuntimeError(f'Unexpected changed entries: {changes}')
    if known and sha(patched_jar) != KNOWN_PATCHED:
        raise RuntimeError('Rebuilt known-version patch differs from the validated artifact; do not install.')
    for jar, label in [(source, 'original'), (patched_jar, 'patched')]:
        disassembly = run([tools['javap'], '-p', '-c', '-classpath', jar, ENTRY[:-6].replace('/', '.')])
        (output / (label + '_javap.txt')).write_text(disassembly + '\n', encoding='utf-8')
    version_file = home / 'VERSION.txt'
    manifest = {'schema': SCHEMA, 'patch_version': 2, 'module': MODULE.as_posix(),
                'original_sha256': source_sha, 'patched_sha256': sha(patched_jar),
                'seadas_version_label': version_file.read_text(encoding='utf-8').strip() if version_file.exists() else 'unknown',
                'rcp_implementation_version': module_version(source), 'locally_validated_original': known,
                'candidate_only': not known, 'changed_entries': changes,
                'source_tool_sha256': {p.name: sha(p) for p in [ROOT / 'src/PatchColorManager.java', ROOT / 'src/PatchInitializationOrder.java']},
                'GUI_validation': 'For this known source hash, the patch matches the locally GUI-validated artifact.' if known else 'Not tested on this version. Structure matching is not semantic or GUI verification.'}
    write_json(output / 'manifest.json', manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


def install(home, package, cache_dir, allow_unverified=False):
    home, package = Path(home).resolve(), Path(package).resolve()
    assert_closed(home)
    manifest = load_manifest(package)
    if manifest.get('candidate_only') and not allow_unverified:
        raise ValueError('Unverified-version candidate: inspect/retest first; use --allow-unverified-version only after review.')
    target, patch = home / MODULE, package / 'patched.jar'
    if sha(patch) != manifest['patched_sha256']:
        raise ValueError('Patch checksum mismatch.')
    current = sha(target)
    if current != manifest['original_sha256']:
        raise ValueError('Installed module does not match this package ORIGINAL hash. It may already be patched or upgraded; never force-overwrite it.')
    backup = home / 'local_fixes/color-manager' / manifest['original_sha256'][:16]
    backup.mkdir(parents=True, exist_ok=True)
    original = backup / 'original.jar'
    if original.exists() and sha(original) != manifest['original_sha256']:
        raise ValueError('Existing original backup checksum mismatch; refusing to overwrite it.')
    if not original.exists():
        with target.open('rb') as src, original.open('xb') as dst:
            shutil.copyfileobj(src, dst)
    if sha(original) != manifest['original_sha256']:
        raise RuntimeError('Original backup verification failed.')
    write_json(backup / 'manifest.json', manifest)
    atomic_copy(patch, target)
    if sha(target) != manifest['patched_sha256']:
        raise RuntimeError(f'Post-install checksum mismatch. Original backup: {original}')
    receipt = {'status': 'installed', 'backup': str(backup), 'target_sha256': sha(target), 'cache_dir': str(Path(cache_dir).resolve())}
    write_json(backup / 'deployment.json', receipt)
    try:
        receipt['cache_backup'] = refresh_cache(cache_dir)
    except OSError as error:
        receipt['cache_refresh_error'] = str(error)
        write_json(backup / 'deployment.json', receipt)
        raise RuntimeError(f'Module installed but cache refresh failed. Keep SeaDAS closed and handle the cache before starting: {error}') from error
    write_json(backup / 'deployment.json', receipt)
    print(json.dumps(receipt, indent=2))
    return receipt


def rollback(home, backup, cache_dir=None):
    home, backup = Path(home).resolve(), Path(backup).resolve()
    assert_closed(home)
    manifest = load_manifest(backup)
    target, original = home / MODULE, backup / 'original.jar'
    if sha(target) != manifest['patched_sha256']:
        raise ValueError('Installed JAR is not exactly this patch; refusing to replace a different upgrade/build.')
    if sha(original) != manifest['original_sha256']:
        raise ValueError('Original backup checksum mismatch.')
    deployment = json.loads((backup / 'deployment.json').read_text(encoding='utf-8'))
    cache_dir = cache_dir or deployment['cache_dir']
    atomic_copy(original, target)
    if sha(target) != manifest['original_sha256']:
        raise RuntimeError('Rollback checksum mismatch.')
    receipt = {'status': 'rolled_back', 'target_sha256': sha(target), 'cache_dir': str(cache_dir)}
    write_json(backup / 'rollback.json', receipt)
    try:
        receipt['cache_backup'] = refresh_cache(cache_dir)
    except OSError as error:
        receipt['cache_refresh_error'] = str(error)
        write_json(backup / 'rollback.json', receipt)
        raise RuntimeError(f'Original module restored, but cache refresh failed: {error}') from error
    write_json(backup / 'rollback.json', receipt)
    print(json.dumps(receipt, indent=2))
    return receipt


def status(args):
    target = args.seadas_home / MODULE
    value = sha(target)
    print(json.dumps({'module': str(target.resolve()), 'sha256': value, 'rcp_implementation_version': module_version(target),
                      'known_local_original': value == KNOWN_ORIGINAL, 'known_local_v2_patch': value == KNOWN_PATCHED,
                      'runtime_loading_verified': False}, indent=2))


def verify_runtime(args):
    tools = jdk_tools(args.jdk_home)
    work = args.output.resolve()
    work.mkdir(parents=True, exist_ok=True)
    classes = work / 'probe_classes'
    run([tools['javac'], '--release', '21', '-d', classes, ROOT / 'src/LoadedClassProbe.java', ROOT / 'src/AttachProbe.java'])
    agent = work / 'probe.jar'
    with zipfile.ZipFile(agent, 'w') as archive:
        archive.writestr('META-INF/MANIFEST.MF', 'Manifest-Version: 1.0\nAgent-Class: LoadedClassProbe\n\n')
        archive.write(classes / 'LoadedClassProbe.class', 'LoadedClassProbe.class')
    report = work / ('loaded-' + str(args.pid) + '.txt')
    run([tools['java'], '-cp', classes, 'AttachProbe', str(args.pid), agent, report])
    text = report.read_text(encoding='utf-8')
    print(text or 'Color Manager class not loaded. Open Color Manager first, then repeat the probe.')
    if 'patched_helper_loaded=false' in text:
        raise ValueError('The running app loaded an old class. Exit normally, preserve/refresh the correct profile cache, then restart.')
    if not text:
        raise ValueError('Color Manager class is not loaded yet.')
    print('The old-view guard is loaded. Complete the RGB/equalize/close/reopen GUI check to verify the full fix.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    b = sub.add_parser('build', help='Generate a local candidate; does not change the installation')
    b.add_argument('--seadas-home', type=Path, required=True)
    b.add_argument('--jdk-home', type=Path, required=True)
    b.add_argument('--input-jar', type=Path, help='Use an intact original backup when the installed module is already patched')
    b.add_argument('--output', type=Path, required=True)
    b.add_argument('--allow-unverified-version', action='store_true')
    i = sub.add_parser('install', help='Exit SeaDAS first; back up original, replace module, refresh class cache')
    i.add_argument('--seadas-home', type=Path, required=True)
    i.add_argument('--package', type=Path, required=True)
    i.add_argument('--cache-dir', type=Path, default=default_cache(), required=default_cache() is None)
    i.add_argument('--allow-unverified-version', action='store_true')
    r = sub.add_parser('rollback', help='Restore an exact original backup and refresh class cache')
    r.add_argument('--seadas-home', type=Path, required=True)
    r.add_argument('--backup', type=Path, required=True)
    r.add_argument('--cache-dir', type=Path)
    s = sub.add_parser('status', help='Read-only installation checksum; not proof of runtime loading')
    s.add_argument('--seadas-home', type=Path, required=True)
    v = sub.add_parser('verify-runtime', help='Attach a read-only class probe to your running SeaDAS JVM')
    v.add_argument('--jdk-home', type=Path, required=True)
    v.add_argument('--pid', type=int, required=True)
    v.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'build':
            build(args)
        elif args.command == 'install':
            install(args.seadas_home, args.package, args.cache_dir, args.allow_unverified_version)
        elif args.command == 'rollback':
            rollback(args.seadas_home, args.backup, args.cache_dir)
        elif args.command == 'status':
            status(args)
        else:
            verify_runtime(args)
        return 0
    except (ValueError, OSError, RuntimeError, zipfile.BadZipFile, KeyError) as error:
        parser.exit(2, f'Error: {error}\n')


if __name__ == '__main__':
    raise SystemExit(main())