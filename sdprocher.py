#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
sdprocher.py - 프로세스 실행 상태 검사기
지정된 프로세스 목록에 대하여 현재 실행 상태를 점검하여 출력.

사용법:
    python sdprocher.py <입력파일.csv|json>
    python sdprocher.py --format json <입력파일>
"""
from __future__ import print_function, unicode_literals

import sys
import os
import csv
import json
import argparse
import datetime

# ---------------------------------------------------------------------------
# Python 2/3 호환 타입 정의
# ---------------------------------------------------------------------------

if sys.version_info[0] >= 3:
    _string_types = (str,)
    _text_type = str
else:
    _string_types = (str, unicode)   # noqa: F821
    _text_type = unicode              # noqa: F821

# ---------------------------------------------------------------------------
# Windows 콘솔 UTF-8 출력 설정
# ---------------------------------------------------------------------------

if sys.platform == 'win32':
    if sys.version_info[0] >= 3:
        for _stream in (sys.stdin, sys.stdout, sys.stderr):
            if hasattr(_stream, 'reconfigure'):
                try:
                    _stream.reconfigure(encoding='utf-8', errors='replace')
                except Exception:
                    pass
    else:
        import codecs as _codecs
        sys.stdout = _codecs.getwriter('utf-8')(sys.stdout)
        sys.stderr = _codecs.getwriter('utf-8')(sys.stderr)

try:
    import psutil
except ImportError:
    print("오류: psutil 패키지가 필요합니다. 설치: pip install psutil", file=sys.stderr)
    sys.exit(1)

try:
    from rich.console import Console
    from rich.table import Table
    from rich import box
    from rich.text import Text
    HAS_RICH = True
except ImportError:
    HAS_RICH = False


# ---------------------------------------------------------------------------
# 입력 파싱
# ---------------------------------------------------------------------------

def detect_format(filepath):
    """파일 확장자로 JSON / CSV 감지. 판별 불가 시 csv 반환"""
    ext = os.path.splitext(filepath)[1].lower()
    if ext == '.json':
        return 'json'
    return 'csv'


def parse_json(fh):
    data = json.loads(fh.read().decode('utf-8'))
    if isinstance(data, dict):
        data = [data]
    return data


def parse_csv(fh):
    raw = fh.read().decode('utf-8')
    if sys.version_info[0] >= 3:
        import io as _io
        reader = csv.DictReader(_io.StringIO(raw))
        return list(reader)
    else:
        # Python 2.7: csv 모듈은 bytes 전용이므로 utf-8로 재인코딩 후 파싱하고
        # 각 키/값을 다시 unicode로 변환한다.
        import io as _io
        lines = raw.encode('utf-8').splitlines(True)
        reader = csv.DictReader(lines)
        result = []
        for row in reader:
            result.append({
                k.decode('utf-8'): v.decode('utf-8')
                for k, v in row.items()
            })
        return result


# 허용하는 필드명 변형들
_FIELD_ALIASES = {
    'run type':     'run_type',
    'run_type':     'run_type',
    'type':         'run_type',
    'runtype':      'run_type',
    'process name': 'process_name',
    'process_name': 'process_name',
    'processname':  'process_name',
    'name':         'process_name',
    'cmd':          'cmd',
    'command':      'cmd',
    'cmdline':      'cmd',
    'path':         'path',
}


def normalize_record(record):
    """필드명 정규화 및 값 공백 제거"""
    out = {}
    for k, v in record.items():
        key = _FIELD_ALIASES.get(k.strip().lower(), k.strip().lower())
        out[key] = v.strip() if isinstance(v, _string_types) else (v or '')
    return out


# ---------------------------------------------------------------------------
# 유틸리티
# ---------------------------------------------------------------------------

def _safe_str(s):
    """인코딩 불가 문자를 '?'로 대체하여 안전한 문자열 반환"""
    if sys.version_info[0] < 3:
        if s is None:
            return u''
        if isinstance(s, bytes):
            return s.decode('utf-8', 'replace')
        return s  # 이미 unicode
    if not isinstance(s, str):
        return str(s) if s is not None else ''
    return s.encode('utf-8', errors='replace').decode('utf-8', errors='replace')


# ---------------------------------------------------------------------------
# 프로세스 검색
# ---------------------------------------------------------------------------

# cmdline의 첫 번째 토큰(실행 파일명)이 아래 목록에 해당하면 매칭에서 제외.
# 뷰어·에디터·페이저 등이 패턴 문자열을 인자로 받아 실행되는 경우를 걸러낸다.
_EXCLUDED_EXECUTABLES = {
    'vim', 'vi', 'nvim', 'nano', 'emacs',
    'less', 'more',
    'tail', 'head',
    'screen', 'tmux',
    'watch',
    'cat', 'grep', 'awk', 'sed',
}


def _is_excluded(cmdline_parts):
    """cmdline 첫 토큰의 basename이 제외 목록에 있으면 True"""
    if not cmdline_parts:
        return False
    exe_name = os.path.basename(cmdline_parts[0]).lower()
    exe_name = os.path.splitext(exe_name)[0]
    return exe_name in _EXCLUDED_EXECUTABLES


def find_procs_by_cmd(cmd_pattern):
    """cmd_pattern 문자열을 포함하는 프로세스 목록 반환.
    뷰어·에디터·페이저 등의 명령어가 첫 토큰인 프로세스는 제외한다."""
    matches = []
    for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'status', 'create_time', 'exe']):
        try:
            parts = proc.info.get('cmdline') or []
            if _is_excluded(parts):
                continue
            cmdline = ' '.join(parts)
            if cmd_pattern and cmd_pattern in cmdline:
                matches.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return matches


def _find_parent(procs):
    """매칭된 프로세스 목록에서 어미 프로세스를 반환한다.

    ppid가 매칭된 PID 집합에 없는 프로세스들을 루트 후보로 수집한다.
    루트 후보가 하나면 그것이 어미이고, 여럿이면 PID가 가장 낮은 것을 반환한다.
    """
    if len(procs) == 1:
        return procs[0]

    pid_set = set(p.pid for p in procs)

    roots = []
    for p in procs:
        try:
            ppid = p.ppid()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if ppid not in pid_set:
            roots.append(p)

    if len(roots) == 1:
        return roots[0]

    # 루트가 여럿(독립 프로세스)이거나 판별 불가 시 PID 최솟값 반환
    candidates = roots if roots else procs
    return min(candidates, key=lambda p: p.pid)


def _fmt_ts(ts):
    """Unix timestamp → 'YYYY-MM-DD HH:MM:SS' 문자열"""
    try:
        return datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError, OSError, OverflowError):
        return ''


def get_proc_detail(proc):
    """psutil.Process 객체에서 필요한 정보 추출"""
    try:
        create_time = _fmt_ts(proc.create_time())
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        create_time = ''

    try:
        status = proc.status()
        is_zombie = (status == psutil.STATUS_ZOMBIE)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        status = 'unknown'
        is_zombie = False

    try:
        cmdline = _safe_str(' '.join(proc.cmdline()))
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        cmdline = ''

    return {
        'pid':         proc.pid,
        'status':      status,
        'is_zombie':   is_zombie,
        'create_time': create_time,
        'cmdline':     cmdline,
    }


# ---------------------------------------------------------------------------
# 점검 로직
# ---------------------------------------------------------------------------

def check_processes(process_list):
    """process_list 각 항목에 대해 실행 상태를 점검하여 결과 목록 반환"""
    results = []

    for raw in process_list:
        item = normalize_record(raw)
        run_type     = item.get('run_type', '')
        process_name = item.get('process_name', '')
        cmd          = item.get('cmd', '')
        path         = item.get('path', '')

        if not cmd:
            results.append({
                'run_type':     run_type,
                'process_name': process_name,
                'running':      False,
                'zombie':       False,
                'create_time':  '',
                'cmd':          cmd,
                'path':         path,
                'pid':          '',
                'count':        0,
            })
            continue

        matched = find_procs_by_cmd(cmd)

        if not matched:
            results.append({
                'run_type':     run_type,
                'process_name': process_name,
                'running':      False,
                'zombie':       False,
                'create_time':  '',
                'cmd':          cmd,
                'path':         path,
                'pid':          '',
                'count':        0,
            })
        else:
            parent = _find_parent(matched)
            detail = get_proc_detail(parent)
            try:
                child_count = len(parent.children())
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                child_count = 0
            results.append({
                'run_type':     run_type,
                'process_name': process_name,
                'running':      not detail['is_zombie'],
                'zombie':       detail['is_zombie'],
                'create_time':  detail['create_time'],
                'cmd':          detail['cmdline'] or cmd,
                'path':         path,
                'pid':          str(detail['pid']),
                'count':        child_count,
            })

    return results


# ---------------------------------------------------------------------------
# 출력
# ---------------------------------------------------------------------------

# 테이블 출력용 헤더 (한글)
_TABLE_HEADERS = ['프로세스 타입', '프로세스명', '프로세스 상태', '좀비', '자식수', '실행 시각', 'cmd']

# CSV / JSON 출력용 영문 키
_OUTPUT_KEYS = ['run_type', 'process_name', 'running', 'zombie', 'child_count', 'start_time', 'cmd']


def _to_output_record(r):
    """결과 dict → 영문 키 출력용 dict"""
    return {
        'run_type':     r['run_type'],
        'process_name': r['process_name'],
        'running':      'Y' if r['running'] else 'N',
        'zombie':       'Y' if r['zombie'] else 'N',
        'child_count':  r['count'],
        'start_time':   r['create_time'],
        'cmd':          r['cmd'],
    }


def _table_row(r):
    return [
        r['run_type'],
        r['process_name'],
        'Y' if r['running'] else 'N',
        'Y' if r['zombie'] else 'N',
        str(r['count']),
        r['create_time'],
        r['cmd'],
    ]


def output_rich(results):
    console = Console(legacy_windows=False)
    table = Table(box=box.ROUNDED, show_header=True, header_style="bold cyan", expand=False)

    table.add_column(_TABLE_HEADERS[0], style="dim", no_wrap=True)
    table.add_column(_TABLE_HEADERS[1], no_wrap=True)
    table.add_column(_TABLE_HEADERS[2], justify="center", no_wrap=True)
    table.add_column(_TABLE_HEADERS[3], justify="center", no_wrap=True)
    table.add_column(_TABLE_HEADERS[4], justify="right", no_wrap=True)
    table.add_column(_TABLE_HEADERS[5], no_wrap=True)
    table.add_column(_TABLE_HEADERS[6])

    for r in results:
        running_text = Text("Y", style="bold green") if r['running'] else Text("N", style="bold red")
        zombie_text  = Text("Y", style="bold yellow") if r['zombie'] else Text("N")
        count_text   = Text(str(r['count']), style="bold yellow") if r['count'] >= 1 else Text(str(r['count']))
        table.add_row(
            r['run_type'],
            r['process_name'],
            running_text,
            zombie_text,
            count_text,
            r['create_time'],
            r['cmd'],
        )

    console.print()
    console.print(table)
    running_cnt = sum(1 for r in results if r['running'])
    console.print(
        "  총 [bold]{0}[/bold]건 점검 완료  "
        "실행 중 [bold green]{1}[/bold green]건 / "
        "미실행 [bold red]{2}[/bold red]건".format(
            len(results), running_cnt, len(results) - running_cnt)
    )
    console.print()


def _terminal_width():
    """현재 터미널 너비를 반환한다. 감지 불가 시 80을 기본값으로 사용."""
    try:
        return os.get_terminal_size().columns
    except (AttributeError, ValueError, OSError):
        # Python 2.7 또는 터미널이 없는 환경
        try:
            import struct, fcntl, termios
            data = fcntl.ioctl(1, termios.TIOCGWINSZ, b'\x00' * 8)
            return struct.unpack('HH', data[:4])[1] or 80
        except Exception:
            return 80


def _truncate(text, width):
    """text를 width 글자 이내로 자르고 초과 시 '...' 접미어를 붙인다."""
    if len(text) <= width:
        return text
    return text[:max(width - 3, 0)] + '...'


def output_plain(results):
    col_widths = [len(h) for h in _TABLE_HEADERS]
    rows = [_table_row(r) for r in results]

    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(_text_type(cell)))

    # 구분자(컬럼 사이 '  ')를 포함한 앞 컬럼들의 총 너비
    COL_SEP = 2
    leading_width = sum(col_widths[:-1]) + COL_SEP * (len(col_widths) - 1) + COL_SEP
    last_col_max = _terminal_width() - leading_width
    # 최소 헤더 너비는 보장
    last_col_max = max(last_col_max, len(_TABLE_HEADERS[-1]))
    col_widths[-1] = min(col_widths[-1], last_col_max)

    fmt = '  '.join('{{:<{0}}}'.format(w) for w in col_widths)
    sep = '  '.join('-' * w for w in col_widths)

    print()
    print(fmt.format(*_TABLE_HEADERS))
    print(sep)
    for row in rows:
        cells = [_text_type(c) for c in row]
        cells[-1] = _truncate(cells[-1], col_widths[-1])
        print(fmt.format(*cells))
    print()
    running_cnt = sum(1 for r in results if r['running'])
    print("총 {0}건 점검 완료  실행 중 {1}건 / 미실행 {2}건".format(
        len(results), running_cnt, len(results) - running_cnt))
    print()


def output_csv(results):
    writer = csv.DictWriter(sys.stdout, fieldnames=_OUTPUT_KEYS, lineterminator='\n')
    writer.writeheader()
    for r in results:
        writer.writerow(_to_output_record(r))


def output_json(results):
    records = [_to_output_record(r) for r in results]
    print(json.dumps(records, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# 진입점
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='프로세스 실행 상태 검사기',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            '예시:\n'
            '  python sdprocher.py procs.csv\n'
            '  python sdprocher.py procs.json\n'
            '  python sdprocher.py --format json procs.txt\n'
            '  python sdprocher.py -o csv procs.csv\n'
            '  python sdprocher.py -o json procs.csv\n'
        ),
    )
    parser.add_argument('input_file', help='입력 파일 경로 (CSV 또는 JSON)')
    parser.add_argument(
        '--format', '-f',
        choices=['csv', 'json'],
        help='입력 형식 강제 지정 (생략 시 자동 감지)',
    )
    parser.add_argument(
        '--output', '-o',
        choices=['table', 'csv', 'json'],
        default='table',
        help='출력 형식: table(기본값), csv, json',
    )
    args = parser.parse_args()

    fmt = args.format or detect_format(args.input_file)
    try:
        with open(args.input_file, 'rb') as fh:
            process_list = parse_json(fh) if fmt == 'json' else parse_csv(fh)
    except IOError as e:
        print("오류: 파일을 열 수 없습니다 - {0}".format(e), file=sys.stderr)
        sys.exit(1)
    except (ValueError, KeyError) as e:
        print("오류: 입력 파싱 실패 ({0}) - {1}".format(fmt, e), file=sys.stderr)
        sys.exit(1)

    if not process_list:
        print("입력에 처리할 항목이 없습니다.", file=sys.stderr)
        sys.exit(0)

    results = check_processes(process_list)

    if args.output == 'csv':
        output_csv(results)
    elif args.output == 'json':
        output_json(results)
    elif HAS_RICH:
        output_rich(results)
    else:
        output_plain(results)


if __name__ == '__main__':
    main()
