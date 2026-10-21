#!/usr/bin/env python3
"""
Собирает dota2-backend (Go) — движок разбора сущностей демок Dota 2.

Что делает:
  1. Проверяет, что установлен Go >= 1.23 (https://go.dev/dl/).
  2. Скачивает библиотеку github.com/dotabuff/manta (парсер Source 2 демок).
  3. Копирует её в backend/manta_patched и вносит 3 маленьких патча:
     a) демки, записанные локально / через SourceTV (game_dir без /dota_vNNNN/),
        больше не падают с "unable to determine game build" — берётся новейший билд;
     b) игровые события можно слушать по маске "*" (все события сразу);
     c) у GameEvent появляются методы Name() и Fields() для полного дампа.
  4. Собирает бинарь backend/dota2-backend(.exe).

Запуск:  python setup_backend.py
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PATCHED = os.path.join(HERE, "manta_patched")

PATCHES = [
    # (файл, было, стало)
    (
        "class.go",
        '''	matches := gameBuildRegexp.FindStringSubmatch(m.GetGameDir())
	if len(matches) < 2 {
		return fmt.Errorf("unable to determine game build from '%s'", m.GetGameDir())
	}''',
        '''	matches := gameBuildRegexp.FindStringSubmatch(m.GetGameDir())
	if len(matches) < 2 {
		// Local / SourceTV / client-recorded demos have a plain install path
		// instead of /dota_vNNNN/. Treat them as the newest known build.
		if p.GameBuild == 0 {
			p.GameBuild = 999999
		}
		return nil
	}''',
    ),
    (
        "game_event.go",
        '''	// Get the handlers for the event name. Return early if none.
	handlers := p.gameEventHandlers[name]
	if handlers == nil {
		return nil
	}''',
        '''	// Get the handlers for the event name. "*" handlers receive everything.
	handlers := append(p.gameEventHandlers[name], p.gameEventHandlers["*"]...)
	if len(handlers) == 0 {
		return nil
	}''',
    ),
    (
        "game_event.go",
        '''// Gets the string value of a named field.
func (e *GameEvent) GetString(name string) (string, error) {''',
        '''// Name returns the game event type name (e.g. "dota_player_kill").
func (ge *GameEvent) Name() string { return ge.t.name }

// Fields returns all fields of the event as a plain map.
func (ge *GameEvent) Fields() map[string]interface{} {
	out := map[string]interface{}{}
	keys := ge.m.GetKeys()
	for name, field := range ge.t.fields {
		if field.i >= len(keys) {
			continue
		}
		key := keys[field.i]
		switch key.GetType() {
		case gameEventTypeString:
			out[name] = key.GetValString()
		case gameEventTypeFloat:
			out[name] = key.GetValFloat()
		case gameEventTypeLong:
			out[name] = key.GetValLong()
		case gameEventTypeShort:
			out[name] = key.GetValShort()
		case gameEventTypeByte:
			out[name] = key.GetValByte()
		case gameEventTypeBool:
			out[name] = key.GetValBool()
		case gameEventTypeUint64:
			out[name] = key.GetValUint64()
		}
	}
	return out
}

// Gets the string value of a named field.
func (e *GameEvent) GetString(name string) (string, error) {''',
    ),
    (
        "envelope_fast.go",
        """			return p.gameEventHandlers[name] == nil""",
        """			return p.gameEventHandlers[name] == nil && p.gameEventHandlers["*"] == nil""",
    ),
]


def run(cmd, **kw):
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True, **kw)


def find_go():
    go = shutil.which("go")
    if go:
        return go
    for cand in (
        "/usr/local/go/bin/go",
        os.path.expanduser("~/go/bin/go"),
        r"C:\Program Files\Go\bin\go.exe",
        os.path.join(HERE, "..", ".tools", "go", "bin", "go"),
        "/home/user/.tools/go/bin/go",
    ):
        if os.path.isfile(cand):
            return cand
    return None


def main():
    go = find_go()
    if not go:
        print("ОШИБКА: не найден Go. Установите Go >= 1.23: https://go.dev/dl/", file=sys.stderr)
        return 1
    env = dict(os.environ)
    # Щадящий режим для машин с малым объёмом памяти (протобаф Dota огромный).
    env.setdefault("GOFLAGS", "-p=1")
    env.setdefault("GOMEMLIMIT", "1600MiB")
    env.setdefault("GOGC", "30")
    tmp = os.path.join(HERE, ".gotmp")
    os.makedirs(tmp, exist_ok=True)
    env.setdefault("TMPDIR", tmp)

    if not os.path.isdir(PATCHED):
        # Тянем manta zip-архивом с Go module proxy напрямую. Через
        # `go mod download` на чистом клоне нельзя: в go.mod уже прописан
        # replace на ./manta_patched, которой ещё не существует.
        import io
        import re
        import urllib.request
        import zipfile
        ver = None
        try:
            with open(os.path.join(HERE, "go.mod"), encoding="utf-8") as f:
                m = re.search(r"github\.com/dotabuff/manta\s+(v[\d.]+)", f.read())
                ver = m.group(1) if m else None
        except OSError:
            pass
        ver = ver or "v1.5.1"
        src = None
        # 1) вдруг manta уже лежит в локальном кэше модулей
        go_mod_cache = subprocess.run(
            [go, "env", "GOMODCACHE"], capture_output=True, text=True, env=env
        ).stdout.strip()
        mod_root = os.path.join(go_mod_cache, "github.com", "dotabuff")
        if os.path.isdir(mod_root):
            # строго закреплённая в go.mod версия, а не любая из кэша
            exact = os.path.join(mod_root, f"manta@{ver}")
            if os.path.isdir(exact):
                src = exact
        if src and os.path.isdir(src):
            print(f"Копирую {src} -> {PATCHED}")
            shutil.copytree(src, PATCHED)
        else:
            # 2) zip с proxy.golang.org
            url = f"https://proxy.golang.org/github.com/dotabuff/manta/@v/{ver}.zip"
            print(f"Скачиваю manta {ver}: {url}")
            try:
                with urllib.request.urlopen(url, timeout=300) as r:
                    blob = r.read()
                zf = zipfile.ZipFile(io.BytesIO(blob))
                prefix = f"github.com/dotabuff/manta@{ver}/"
                os.makedirs(PATCHED, exist_ok=True)
                for info in zf.infolist():
                    if not info.filename.startswith(prefix) or info.is_dir():
                        continue
                    rel = info.filename[len(prefix):]
                    dst = os.path.join(PATCHED, rel)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    with open(dst, "wb") as out_f:
                        out_f.write(zf.read(info))
            except Exception as ex:
                print(f"ОШИБКА: не удалось получить manta ({ex}).\n"
                      f"Проверьте интернет или скачайте вручную: {url}", file=sys.stderr)
                return 1
        # module cache — read-only
        for root, dirs, files in os.walk(PATCHED):
            os.chmod(root, 0o755)
            for fn in files:
                os.chmod(os.path.join(root, fn), 0o644)
    # Патчи применяем при каждом запуске (идемпотентно) — на случай, если
    # manta_patched уже существует от прошлого запуска без какого-то патча.
    for fname, old, new in PATCHES:
        path = os.path.join(PATCHED, fname)
        text = open(path, encoding="utf-8").read()
        if new in text:
            continue
        if old not in text:
            print(f"ОШИБКА: патч не применился к {fname} (manta обновилась?)", file=sys.stderr)
            return 1
        open(path, "w", encoding="utf-8").write(text.replace(old, new))
        print(f"Патч применён: {fname}")

    # (на случай повторного запуска поверх уже пропатченной копии)
    class_go = os.path.join(PATCHED, "class.go")
    if os.path.isfile(class_go):
        text = open(class_go, encoding="utf-8").read()
        if "fmt." not in text and '\t"fmt"\n' in text:
            open(class_go, "w", encoding="utf-8").write(text.replace('\t"fmt"\n', ""))

    run([go, "mod", "edit", "-replace", "github.com/dotabuff/manta=./manta_patched"], cwd=HERE, env=env)
    out = os.path.join(HERE, "dota2-backend.exe" if os.name == "nt" else "dota2-backend")
    run([go, "mod", "tidy"], cwd=HERE, env=env)
    run([go, "build", "-o", out, "."], cwd=HERE, env=env)
    print(f"\nГотово: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
